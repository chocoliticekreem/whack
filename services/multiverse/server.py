"""Multiverse HTTP API (stdlib) on :8049.

POST /spawn      {n=50, horizonMin=30, mode="replay"|"live", crnSeed=47, rngSeed=7, brokers="on"|"off"}
POST /observe    {next: true} (replay: consume the next recorded frame)
                 | {ts, readings: {camId: people}, busDelayMin?} (push a frame)
                 | {} in live mode (poll the twin's /api/around/counts now)
GET  /state      exchange board, consensus, implied volatility, recent observations
POST /intervene  {kind, zoneId, atMin, note?, prior=0.5}: fork every live world
GET  /replay     ?from=<ms>&to=<ms>: the recorded frames available (timestamps + camera counts)
GET  /geometry   street segments, zones and cameras of the current seed (for the map)
GET  /brokers    persona-broker leaderboard (track record, idea, conviction, rationale, P&L) + Haiku calls
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import brokers
import engine
import feeds
import fixture
from worlds import seedmod

PORT = int(os.environ.get("MULTIVERSE_PORT", "8049"))
ALLOWED_ORIGIN = os.environ.get("MULTIVERSE_CORS_ORIGIN", "http://localhost:3047")
TWIN_URL = os.environ.get("TWIN_URL", "http://localhost:3047")
LIVE_ORIGIN = (51.5152, -0.1419)
MAX_N = 200


class App:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.mv: engine.Multiverse | None = None
        self.mode = "replay"
        self.frames: list[dict] = []
        self.cursor = 0
        self.raw_root: Path | None = None

    def replay_seed(self) -> tuple[dict, Path]:
        seed_path = os.environ.get("MULTIVERSE_SEED")
        if seed_path:
            raw = Path(os.environ.get("MULTIVERSE_RAW") or feeds.REPO / "data" / "raw")
            return json.loads(Path(seed_path).read_text()), raw
        if not fixture.SEED_PATH.is_file():
            fixture.main()
        return json.loads(fixture.SEED_PATH.read_text()), Path(os.environ.get("MULTIVERSE_RAW") or fixture.RAW_DIR)

    def spawn(self, body: dict) -> dict:
        n = max(2, min(int(body.get("n") or engine.DEFAULT_N), MAX_N))
        horizon = max(5, min(int(body.get("horizonMin") or 30), 120))
        mode = body.get("mode") or "replay"
        if mode not in ("replay", "live"):
            raise ValueError("mode must be replay or live")
        if mode == "replay":
            seed, self.raw_root = self.replay_seed()
            self.frames = feeds.load_frames(self.raw_root)
            if self.frames and not (seed["at"] <= self.frames[0]["ts"] <= seed["at"] + horizon * 60_000):
                seed = engine.reseed(seed, self.frames[0]["ts"], self.frames[0]["readings"])
                self.frames = self.frames[1:]
        else:
            seedmod.TWIN_URL = TWIN_URL
            seed = seedmod.build_seed([], LIVE_ORIGIN, "Oxford Circus")
            self.frames, self.raw_root = [], None
        flag = body.get("brokers", "on")
        if flag not in ("on", "off", True, False):
            raise ValueError("brokers must be on or off")
        brokerage = brokers.Brokerage(seed, horizon) if flag in ("on", True) else None
        self.mode, self.cursor = mode, 0
        mv = engine.Multiverse(seed, n=n, horizon=horizon, crn_seed=int(body.get("crnSeed") or 47),
                               rng_seed=int(body.get("rngSeed") or 7), brokerage=brokerage)
        mv.spawn()
        self.mv = mv
        return self.state()

    def need(self) -> engine.Multiverse:
        if self.mv is None:
            raise LookupError("no multiverse yet: POST /spawn first")
        return self.mv

    def observe(self, body: dict) -> dict:
        mv = self.need()
        if body.get("next"):
            if self.cursor >= len(self.frames):
                return {"done": True, "observation": None, "remaining": 0, **self.state()}
            frame = self.frames[self.cursor]
            self.cursor += 1
        elif "readings" in body:
            readings = body["readings"]
            if isinstance(readings, list):
                readings = feeds.readings_of(readings)
            if not isinstance(readings, dict):
                raise ValueError("readings must be {camId: people} or [{camId, people}]")
            frame = {"ts": int(body.get("ts") or mv.seed["at"] + 60_000 * (mv.now_minute() + 1)),
                     "readings": {str(k): float(v) for k, v in readings.items()},
                     "busDelayMin": body.get("busDelayMin")}
        elif self.mode == "live":
            frame = feeds.live_frame(TWIN_URL, list(mv.cam_names))
            frame["ts"] = frame["ts"] or mv.seed["at"]
        else:
            raise ValueError("replay mode: send {next: true} or {ts, readings}")
        if frame["ts"] > mv.seed["at"] + mv.horizon * 60_000:
            mv.roll(engine.reseed(mv.seed, frame["ts"], frame["readings"]))
        obs = mv.observe(frame["ts"], frame["readings"], frame.get("busDelayMin"))
        return {"observation": obs, "remaining": len(self.frames) - self.cursor, **self.state()}

    def intervene(self, body: dict) -> dict:
        mv = self.need()
        zone = body.get("zoneId") or next((z["id"] for z in mv.seed["zones"] if z["name"] == body.get("zoneName")), None)
        forked = mv.intervene(str(body.get("kind") or "incident"), str(zone), float(body.get("atMin") or mv.now_minute()),
                              body.get("note"), float(body.get("prior") or engine.FORK_PRIOR))
        return {"forked": forked, **self.state()}

    def state(self) -> dict:
        mv = self.need()
        return {"mode": self.mode, "replay": {"frames": len(self.frames), "cursor": self.cursor,
                                              "nextTs": self.frames[self.cursor]["ts"] if self.cursor < len(self.frames) else None},
                **mv.state()}

    def replay(self, q: dict) -> dict:
        root = self.raw_root or self.replay_seed()[1]
        start = int(q["from"][0]) if "from" in q else None
        end = int(q["to"][0]) if "to" in q else None
        frames = feeds.load_frames(root, start, end)
        return {"root": str(root), "frames": [{"ts": f["ts"], "cams": len(f["readings"]), "busDelayMin": f["busDelayMin"]}
                                              for f in frames]}

    def brokers(self) -> dict:
        mv = self.need()
        bk = mv.brokerage
        if bk is None:
            return {"enabled": False, "brokers": [], "calls": []}
        return {"enabled": True, "llm": bk.llm_enabled, "model": brokers.MODEL if bk.llm_enabled else None,
                "brokers": bk.leaderboard(mv.live()), "calls": bk.calls[-10:]}

    def geometry(self) -> dict:
        mv = self.need()
        segs = [{"id": s["id"], "kind": s["kind"], "line": s["line"]} for s in mv.seed["segments"] if s["kind"] != "rail"]
        return {"segments": segs, "zones": mv.state()["zones"],
                "cams": [{k: c.get(k) for k in ("id", "name", "x", "z")} for c in mv.seed.get("cams", [])]}


APP = App()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"{self.command} {self.path} {args[1] if len(args) > 1 else ''}\n")

    def _send(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self._send(204, {})

    def handle_any(self, fn) -> None:
        try:
            with APP.lock:
                self._send(200, fn())
        except LookupError as exc:
            self._send(409, {"error": str(exc)})
        except (ValueError, TypeError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:
            self._send(502, {"error": f"{type(exc).__name__}: {exc}"})

    def do_GET(self) -> None:
        url = urllib.parse.urlparse(self.path)
        path, q = url.path.rstrip("/"), urllib.parse.parse_qs(url.query)
        routes = {"/health": lambda: {"ok": True, "spawned": APP.mv is not None, "mode": APP.mode},
                  "/state": APP.state, "/replay": lambda: APP.replay(q), "/geometry": APP.geometry,
                  "/brokers": APP.brokers}
        if path in routes:
            self.handle_any(routes[path])
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
        routes = {"/spawn": APP.spawn, "/observe": APP.observe, "/intervene": APP.intervene}
        if path in routes:
            self.handle_any(lambda: routes[path](body))
        else:
            self._send(404, {"error": "not found"})


def main() -> None:
    print(f"multiverse on :{PORT} (twin {TWIN_URL})", file=sys.stderr)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
