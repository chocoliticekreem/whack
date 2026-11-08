"""City markets HTTP API (stdlib) on 127.0.0.1:8051 (CITY_HOST, CITY_PORT). Contract: GET /hotspots /traders
/markets /round /leaderboard /stats /signals, POST /settle /bet, wallet routes behind X-Player-Token. Live by
default; --replay-from <ISO> fakes "now" from recorded data at 12x, with a throwaway in-memory ledger.

    python3 services/city/server.py
    python3 services/city/server.py --replay-from 2026-11-07T14:00:00Z
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import threading
import time
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import backup
import signals
from data import MULTIVERSE_URL, SIM_POLL_S, SIM_URL, SimFeed, Store, feeds, fetch_vix, load_hotspots, raw_root
from exchange import Clock, Exchange, round_for
from ledger import AuthError, Ledger, LedgerInUse, get_routes, post_routes, versus

HOST = os.environ.get("CITY_HOST") or "127.0.0.1"
PORT = int(os.environ.get("CITY_PORT", "8051"))
ORIGINS = {"http://localhost:3047", "http://localhost:3349", "http://localhost:3849",
           "http://127.0.0.1:3047", "http://127.0.0.1:3349", "http://127.0.0.1:3849"}
TICK_S = 1.0
VIX_POLL_S = 15.0
VIX_BOOT_TIMEOUT_S = 5.0
MAX_BODY = 16 * 1024
WALLET_GET = {"/wallet", "/ledger", "/positions", "/history"}
WALLET_POST = {"/bet", "/limits", "/break", "/player/name", "/player/pin"}
LOOPBACK = {"127.0.0.1", "::1"}

LOCK = threading.Lock()
LOCK_WARN_S = 0.5
EX: Exchange | None = None
VIX_INBOX: deque = deque(maxlen=1)  # newest VIX history from the poller; only the ticker writes it into the store


@contextlib.contextmanager
def held(what: str):
    """LOCK, with a stderr line whenever one holder keeps it past LOCK_WARN_S (every /bet waits that long)."""
    with LOCK:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            took = time.perf_counter() - t0
            if took > LOCK_WARN_S:
                print(f"lock held {took * 1000:.0f} ms by {what}", file=sys.stderr)


class RateLimit:
    """At most `n` hits per key in a sliding `window_s`."""

    def __init__(self, n: int, window_s: float, clock=time.monotonic):
        self.n, self.window, self.clock = n, window_s, clock
        self.hits: dict[str, deque] = {}
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        with self.lock:
            now, q = self.clock(), self.hits.setdefault(key, deque())
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.n:
                return False
            q.append(now)
            return True


# Why: at a venue every phone on the Wi-Fi shares one public IP through NAT, so these caps are per network, not per
# person: a burst limit that lets a room join at once, and a daily ceiling.
NEW_PLAYERS = RateLimit(int(os.environ.get("CITY_NEWPLAYER_PER_MIN") or 30), 60)
NEW_PLAYERS_DAY = RateLimit(int(os.environ.get("CITY_NEWPLAYER_PER_DAY") or 500), 86_400)
RECOVER_IP = RateLimit(20, 600)  # name + PIN tries per network; the ledger also locks a name after 5 misses


class TooMany(Exception):
    pass


class Server(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


def open_ledger(replay: bool) -> Ledger:
    """Replays are throwaway: they never touch the real ledger file."""
    return Ledger(":memory:") if replay else Ledger.from_env()


def step(ex: Exchange, now: int | None = None) -> None:
    """One tick in three parts, so a round open never holds the lock: under it, read new data and settle; off it,
    build the next round's markets (seconds on a loaded machine); under it again, list them and let the AI trade."""
    now = ex.clock.now() if now is None else now
    with held("tick: data and settlement"):
        while VIX_INBOX:
            ex.store.ingest_vix(VIX_INBOX.popleft())
        ex.advance(now)
        due = round_for(now) not in ex.rounds
    plan = ex.build_round(now) if due else None
    with held("tick: list round and AI trades"):
        if plan:
            ex.list_round(plan)
        ex.trade(now)


def ticker() -> None:
    while True:
        try:
            step(EX)
        except Exception as exc:  # noqa: BLE001 - one bad tick must not stop the market
            print(f"tick failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        time.sleep(TICK_S)


def prime_vix(store: Store, fetch=fetch_vix) -> bool:
    """Live only: one VIX fetch before the first round opens, so london_vix lists in it instead of being skipped
    for "no fresh reading" until the poller's first pass. At most VIX_BOOT_TIMEOUT_S; down → the round goes ahead."""
    got = fetch(MULTIVERSE_URL, timeout=VIX_BOOT_TIMEOUT_S)
    if got:
        store.ingest_vix(got)
    return bool(got)


def boot(ex: Exchange, fetch=fetch_vix) -> tuple[Exchange, dict]:
    """Recover open bets, then open the first round. Live, the VIX and the simulation are fetched after recover():
    its first read of the day's recorder files takes 30-80 s, and a frame fetched before it was already past the
    3-min staleness limit when the first round was built, so london_vix sat out the round after every restart."""
    recovered = ex.recover()
    if ex.clock.live:
        if ex.store.sim_feed:
            ex.store.sim_feed.poll()
        prime_vix(ex.store, fetch)
    ex.tick()
    return ex, recovered


def vix_poller() -> None:
    """Live only: fetch the multiverse's London VIX history every VIX_POLL_S outside the lock, so a slow engine never
    stalls the market. The ticker copies it into the store, which keeps every store write on the ticker thread
    (build_round reads the store off the lock)."""
    while True:
        got = fetch_vix(MULTIVERSE_URL)
        if got:
            VIX_INBOX.append(got)
        time.sleep(VIX_POLL_S)


def sim_poller(feed: SimFeed) -> None:
    """Live only: refresh the central-London simulation forecast every SIM_POLL_S outside the lock (one reference
    swap, so the market never waits on the sim)."""
    while True:
        feed.poll()
        time.sleep(SIM_POLL_S)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("CITY_LOG"):
            sys.stderr.write(f"{self.command} {self.path} {args[1] if len(args) > 1 else ''}\n")

    def _send(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        origin = self.headers.get("Origin")
        if origin in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Player-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self._send(204, {})

    def client_ip(self) -> str:
        """The Next proxy is the only loopback caller that forwards for someone else; trust its X-Forwarded-For."""
        peer = self.client_address[0]
        fwd = self.headers.get("X-Forwarded-For")
        # debt: Next only fills X-Forwarded-For when the client sent none, so a client can set its own and dodge
        # the /player/new limit. ceiling: fine for a points demo. upgrade: a public deploy behind a trusted edge.
        if peer in LOOPBACK and fwd:
            return fwd.split(",")[-1].strip() or peer
        return peer

    def player(self, claimed) -> str:
        return EX.ledger.auth(claimed, self.headers.get("X-Player-Token"))

    def positions(self, q: dict) -> dict:
        player = self.player(q.get("player"))
        return get_routes(EX.ledger, q, player)["/positions"]() | {"pending": EX.integrity.pending_json(player)}

    def new_player(self, body: dict) -> dict:
        ip = self.client_ip()
        if not NEW_PLAYERS.allow(ip):  # burst first, so a refused burst never spends the daily allowance
            raise TooMany("Too many new players from this network, try again in a minute")
        if not NEW_PLAYERS_DAY.allow(ip):
            raise TooMany("Too many new players from this network today, try again tomorrow")
        return EX.ledger.create_player(body.get("name"))

    def recover(self, body: dict) -> dict:
        if not RECOVER_IP.allow(self.client_ip()):
            raise TooMany("Too many sign-in tries from this network, try again in a few minutes")
        return EX.ledger.recover(body.get("name"), body.get("pin"))

    def run(self, fn) -> None:
        try:
            with held(f"{self.command} {urllib.parse.urlparse(self.path).path}"):
                status, data = 200, fn()
        except AuthError as exc:
            status, data = 401, {"ok": False, "error": str(exc)}
        except TooMany as exc:
            status, data = 429, {"ok": False, "error": str(exc)}
        except PermissionError as exc:
            code = getattr(exc, "code", None)  # wallet hook: ledger.WalletError carries a stable code
            status, data = 409, {"ok": False, "error": str(exc)} | ({"code": code} if code else {}) | \
                getattr(exc, "data", {})
        except LookupError as exc:
            status, data = 404, {"ok": False, "error": str(exc)}
        except (ValueError, TypeError, OverflowError) as exc:
            status, data = 400, {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            status, data = 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self._send(status, data)  # outside LOCK: a slow client never stalls the exchange

    def do_GET(self) -> None:
        url = urllib.parse.urlparse(self.path)
        path = url.path.rstrip("/")
        q = dict(urllib.parse.parse_qsl(url.query))
        routes = {"/health": lambda: {"ok": True, "live": EX.clock.live, "now": EX.clock.now()},
                  "/hotspots": EX.hotspots_json, "/traders": EX.traders_json, "/markets": EX.markets_json,
                  "/leaderboard": EX.leaderboard, "/stats": EX.stats_json, "/round": EX.round_json,
                  "/signals": lambda: signals.strip(EX.store, EX.spots, EX.clock.now()),
                  "/local": lambda: EX.local.list_json(q.get("round"))}
        if path == "/bet":  # a bet's pending -> filled / cancelled state
            self.run(lambda: EX.integrity.lookup(EX.ledger, self.player(q.get("player")), q.get("id")))
        elif path == "/positions":
            self.run(lambda: self.positions(q))
        elif path in WALLET_GET:  # wallet hook: reads prove the token and never create a player
            self.run(lambda: get_routes(EX.ledger, q, self.player(q.get("player")))[path]())
        elif path == "/versus":
            self.run(lambda: versus(EX.ledger, self.player(q.get("player")), EX.markets))
        elif path in routes:
            self.run(routes[path])
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.close_connection = True
                self._send(413, {"error": f"body over {MAX_BODY} bytes"})
                return
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
            return
        if path == "/bet":
            self.run(lambda: EX.bet(body | {"player": self.player(body.get("player"))}))
        elif path in WALLET_POST:
            self.run(lambda: post_routes(EX.ledger, body, self.player(body.get("player")))[path]())
        elif path == "/player/new":
            self.run(lambda: self.new_player(body))
        elif path == "/player/recover":
            self.run(lambda: self.recover(body))
        elif path == "/local":
            self.run(lambda: EX.local.create(body, self.player(body.get("player"))))
        elif path == "/settle":
            self.run(lambda: EX.settle_request(str(body.get("id"))))
        else:
            self._send(404, {"error": "not found"})


def main() -> None:
    global EX
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--replay-from", help="ISO time to replay recorded data from, 1 min per 5 s")
    args = ap.parse_args()
    start = feeds.parse_ts(args.replay_from) if args.replay_from else None
    root = raw_root()
    hotspots = load_hotspots()
    try:
        ledger = open_ledger(start is not None)  # wallet hook: CITY_LEDGER_DB, default services/city/data/ledger.db
    except LedgerInUse as exc:
        sys.exit(str(exc))
    store = Store(root, hotspots)
    if start is None:
        store.sim_feed = SimFeed()
    EX, recovered = boot(Exchange(store, Clock(start), hotspots, ledger))
    threading.Thread(target=ticker, daemon=True).start()
    if start is None:
        threading.Thread(target=vix_poller, daemon=True).start()
        threading.Thread(target=sim_poller, args=(store.sim_feed,), daemon=True).start()
        threading.Thread(target=EX.local.streets.run, args=(round_for, EX.clock.now), daemon=True).start()
        backup.start(ledger.path)
    mode = (f"replay from {args.replay_from} at 12x" if start
            else f"live, VIX from {MULTIVERSE_URL}, city simulation from {SIM_URL}")
    print(f"city markets on {HOST}:{PORT} ({mode}, data {root}, ledger {ledger.path}, "
          f"{recovered['pending']} open bets to settle from data, {recovered['refunded']} refunded)", file=sys.stderr)
    Server((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
