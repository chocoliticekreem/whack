"""City markets HTTP API (stdlib) on :8051 (CITY_PORT). Contract: GET /hotspots /traders /markets /round
/leaderboard /stats /signals, POST /settle /bet /score. Live by default; --replay-from <ISO> fakes "now" from recorded
data at 12x.

    python3 services/city/server.py
    python3 services/city/server.py --replay-from 2026-11-07T14:00:00Z
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import signals
from data import MULTIVERSE_URL, Store, feeds, fetch_vix, load_hotspots, raw_root
from exchange import Clock, Exchange
from ledger import Ledger, get_routes, post_routes

PORT = int(os.environ.get("CITY_PORT", "8051"))
ORIGINS = {"http://localhost:3047", "http://localhost:3349", "http://localhost:3849",
           "http://127.0.0.1:3047", "http://127.0.0.1:3349", "http://127.0.0.1:3849"}
TICK_S = 1.0
VIX_POLL_S = 15.0

LOCK = threading.Lock()
EX: Exchange | None = None


def ticker() -> None:
    while True:
        with LOCK:
            try:
                EX.tick()
            except Exception as exc:  # noqa: BLE001 - one bad tick must not stop the market
                print(f"tick failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        time.sleep(TICK_S)


def vix_poller() -> None:
    """Live only: copy the multiverse's London VIX history into the store every VIX_POLL_S (fetched outside the
    lock, so a slow engine never stalls the market)."""
    while True:
        got = fetch_vix(MULTIVERSE_URL)
        if got:
            with LOCK:
                EX.store.ingest_vix(got)
        time.sleep(VIX_POLL_S)


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
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self._send(204, {})

    def run(self, fn) -> None:
        try:
            with LOCK:
                self._send(200, fn())
        except PermissionError as exc:
            code = getattr(exc, "code", None)  # wallet hook: ledger.WalletError carries a stable code
            self._send(409, {"ok": False, "error": str(exc)} | ({"code": code} if code else {}))
        except LookupError as exc:
            self._send(404, {"ok": False, "error": str(exc)})
        except (ValueError, TypeError) as exc:
            self._send(400, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def do_GET(self) -> None:
        url = urllib.parse.urlparse(self.path)
        path = url.path.rstrip("/")
        routes = {"/health": lambda: {"ok": True, "live": EX.clock.live, "now": EX.clock.now()},
                  "/hotspots": EX.hotspots_json, "/traders": EX.traders_json, "/markets": EX.markets_json,
                  "/leaderboard": EX.leaderboard, "/stats": EX.stats_json, "/round": EX.round_json,
                  "/signals": lambda: signals.strip(EX.store, EX.spots, EX.clock.now())}
        routes |= get_routes(EX.ledger, dict(urllib.parse.parse_qsl(url.query)))  # wallet hook
        if path in routes:
            self.run(routes[path])
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
            return
        routes = {"/settle": lambda: EX.settle_request(str(body.get("id"))), "/bet": lambda: EX.bet(body),
                  "/score": lambda: EX.score(body)}
        routes |= post_routes(EX.ledger, body)  # wallet hook
        if path in routes:
            self.run(routes[path])
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
    ledger = Ledger.from_env()  # wallet hook: CITY_LEDGER_DB, default services/city/data/ledger.db
    refunded = ledger.void_open("Exchange restarted before this market settled; stake refunded")
    EX = Exchange(Store(root, hotspots), Clock(start), hotspots, ledger)
    EX.tick()
    threading.Thread(target=ticker, daemon=True).start()
    if start is None:
        threading.Thread(target=vix_poller, daemon=True).start()
    mode = f"replay from {args.replay_from} at 12x" if start else f"live, VIX from {MULTIVERSE_URL}"
    print(f"city markets on :{PORT} ({mode}, data {root}, ledger {ledger.path}, {refunded} open bets refunded)",
          file=sys.stderr)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
