"""The parallel exchange venue over HTTP (:8052), next to the live LMSR exchange (:8051). See venue.py.

It reads the same recorder files read-only and keeps everything else (order books, ledger, accounts) in memory, so it
can run beside the live exchange without touching it, and a restart starts it clean.

    python3 services/city/venue_server.py                                    # live, beside server.py
    python3 services/city/venue_server.py --replay-from 2026-11-07T15:00:00Z  # replay at 12x

GET  /health /contracts /book?contract= /stats /leaderboard /positions?account= (X-Player-Token)
POST /account/new {name}  /order {account, contract, side, price, qty, tif, postOnly}  /cancel {account, order}
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import venue_agents
from data import MULTIVERSE_URL, SIM_POLL_S, SimFeed, Store, feeds, fetch_vix, load_hotspots, raw_root
from exchange import Clock
from server import RateLimit
from venue import CitySource, ShadowExchange, Venue
from venue_gateway import Account

HOST = os.environ.get("CITY_VENUE_HOST") or "127.0.0.1"
PORT = int(os.environ.get("CITY_VENUE_PORT", "8052"))
TICK_S = 1.0
VIX_POLL_S = 15.0
MAX_BODY = 16 * 1024
GRANT = 1000
KEEP_SETTLED_MS = 60_000
LOCK = threading.Lock()
NEW_ACCOUNTS = RateLimit(int(os.environ.get("CITY_NEWPLAYER_PER_MIN") or 30), 60)
V: Venue | None = None
CLOCK: Clock | None = None
TOKENS: dict[str, str] = {}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def contracts_json(now: int) -> dict:
    rows = []
    for cid, c in V.contracts.items():
        if c.done and now - (c.market.settled_at or now) > KEEP_SETTLED_MS:
            continue
        book = V.books[cid]
        rows.append(c.public() | {"status": V.contract_status(cid, now), "forecast": round(V.forecast(cid), 4),
                                  "bid": book.bid(), "ask": book.ask(), "last": book.last,
                                  "depth": book.depth(5), "outcome": c.outcome})
    return {"now": now, "live": CLOCK.live, "contracts": rows}


def leaderboard() -> dict:
    cl = V.clearing
    rows = [{"account": a.id, "name": a.name, "label": a.label(), "equityAtCost": cl.equity_at_cost(a.id),
             "realised": cl.realised.get(a.id, 0)} for a in V.accounts.values()]
    rows.sort(key=lambda r: -r["equityAtCost"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    humans = [r for r in rows if r["label"] == "Player"]
    return {"accounts": len(rows), "top": rows[:50], "players": humans}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args) -> None:
        if os.environ.get("CITY_LOG"):
            super().log_message(fmt, *args)

    def send(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authed(self, account) -> str:
        token = self.headers.get("X-Player-Token") or ""
        want = TOKENS.get(str(account))
        if not want or not hmac.compare_digest(want, token_hash(token)):
            raise PermissionError("unknown account or wrong token")
        return str(account)

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise OverflowError
        got = json.loads(self.rfile.read(n) or b"{}")
        if not isinstance(got, dict):
            raise ValueError("body must be a JSON object")
        return got

    def do_GET(self) -> None:
        url = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        with LOCK:
            now = CLOCK.now()
            try:
                if url.path == "/health":
                    return self.send(200, {"ok": True, "now": now, "live": CLOCK.live, "ticks": V.ticks,
                                           "contracts": len(V.contracts), "accounts": len(V.accounts)})
                if url.path == "/contracts":
                    return self.send(200, contracts_json(now))
                if url.path == "/book":
                    book = V.books.get(q.get("contract", ""))
                    if book is None:
                        return self.send(404, {"error": "unknown contract"})
                    return self.send(200, book.depth(10) | {"tape": [f.public() for f in book.tape[-20:]]})
                if url.path == "/stats":
                    return self.send(200, V.stats())
                if url.path == "/leaderboard":
                    return self.send(200, leaderboard())
                if url.path == "/positions":
                    return self.send(200, V.positions(self.authed(q.get("account"))))
                return self.send(404, {"error": "unknown route"})
            except PermissionError as exc:
                return self.send(401, {"error": str(exc)})

    def do_POST(self) -> None:
        url = urllib.parse.urlparse(self.path)
        try:
            body = self.body()
        except OverflowError:
            return self.send(413, {"error": "body too large"})
        except ValueError as exc:
            return self.send(400, {"error": f"bad JSON: {exc}"})
        with LOCK:
            now = CLOCK.now()
            try:
                if url.path == "/account/new":
                    if not NEW_ACCOUNTS.allow(self.client_address[0]):
                        return self.send(429, {"error": "too many new accounts from this address"})
                    aid = f"p-{secrets.token_hex(6)}"
                    token = secrets.token_urlsafe(24)
                    V.open_account(Account(aid, "human", name=str(body.get("name") or aid)[:24]), GRANT, now)
                    TOKENS[aid] = token_hash(token)
                    return self.send(200, {"account": aid, "token": token, "cash": GRANT})
                if url.path == "/order":
                    acct = self.authed(body.get("account"))
                    o = V.gateway.submit(acct, str(body.get("contract")), str(body.get("side")), body.get("price"),
                                         body.get("qty"), now, str(body.get("tif") or "gtc"),
                                         bool(body.get("postOnly")))
                    return self.send(200 if o.status == "pending" else 409, o.public())
                if url.path == "/cancel":
                    acct = self.authed(body.get("account"))
                    ok, why = V.gateway.cancel(acct, str(body.get("order")), now)
                    return self.send(200 if ok else 409, {"ok": ok, "reason": why})
                return self.send(404, {"error": "unknown route"})
            except PermissionError as exc:
                return self.send(401, {"error": str(exc)})


def ticker() -> None:
    while True:
        with LOCK:
            try:
                V.tick(CLOCK.now())
            except Exception as exc:  # noqa: BLE001 - one bad tick must not stop the venue
                print(f"venue tick failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        time.sleep(TICK_S)


def vix_poller(store: Store) -> None:
    """Live only: the same London VIX the live exchange polls, so london_vix lists here too."""
    while True:
        got = fetch_vix(MULTIVERSE_URL)
        if got:
            with LOCK:
                store.ingest_vix(got)
        time.sleep(VIX_POLL_S)


def sim_poller(feed: SimFeed) -> None:
    while True:
        feed.poll()
        time.sleep(SIM_POLL_S)


def main() -> None:
    global V, CLOCK
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--replay-from", help="ISO time to replay recorded data from, 1 min per 5 s")
    ap.add_argument("--retail", type=int, default=2000)
    ap.add_argument("--market-makers", type=int, default=12)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()
    start = feeds.parse_ts(args.replay_from) if args.replay_from else None
    root, hotspots = raw_root(), load_hotspots()
    store = Store(root, hotspots)
    if start is None:
        store.sim_feed = SimFeed()
        store.sim_feed.poll()
        got = fetch_vix(MULTIVERSE_URL, timeout=5.0)
        if got:
            store.ingest_vix(got)
    CLOCK = Clock(start)
    V = Venue(CitySource(ShadowExchange(store, CLOCK, hotspots)), seed=args.seed)
    venue_agents.populate(V, market_makers=args.market_makers, retail=args.retail, seed=args.seed, now=CLOCK.now())
    V.tick(CLOCK.now())
    threading.Thread(target=ticker, daemon=True).start()
    if start is None:
        threading.Thread(target=vix_poller, args=(store,), daemon=True).start()
        threading.Thread(target=sim_poller, args=(store.sim_feed,), daemon=True).start()
    mode = f"replay from {args.replay_from} at 12x" if start else "live"
    print(f"exchange venue on {HOST}:{PORT} ({mode}, data {root}, {len(V.accounts)} AI accounts, no house)",
          file=sys.stderr)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
