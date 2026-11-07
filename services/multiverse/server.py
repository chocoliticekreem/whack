"""Multiverse HTTP API (stdlib) on :8049.

POST /spawn      {n=50, horizonMin=5, mode="replay"|"live", crnSeed=47, rngSeed=7, brokers="on"|"off",
                  from?, to? (ms, real replay window; default the 45 min with most YOLO counts)}
                 Default (or MULTIVERSE_SEED set to a real seed): seeds/oxford-circus.json on the recorder's data;
                 replay reads `jamcams`, `bus`, `rain_now`, `crowding`, `bikepoint`; live reseeds at the newest
                 recorder snapshot and polls it every 60 s. The synthetic fixture only when no jamcams is recorded.
POST /observe    {next: true} (replay: consume the next recorded frame)
                 | {ts, readings: {camId: people}, busDelayMin?, rainMmH?, crowding?, crowdingTypical?, bikesOut?}
                   (push a frame)
                 | {} in live mode (poll the twin's /api/around/counts now)
GET  /state      exchange board, consensus, implied volatility, recent observations, vix, vix_history
GET  /vix        the London VIX now, its history and what moved it
POST /intervene  {kind, zoneId, atMin, note?, prior=0.5}: fork every live world
GET  /replay     ?from=<ms>&to=<ms>: the recorded frames available (timestamps + camera counts)
GET  /geometry   street segments, zones and cameras of the current seed (for the map)
GET  /brokers    data-source broker leaderboard (track record, idea, conviction, rationale, P&L) + Haiku calls

Central London (MULTIVERSE_SEED=seeds/central-london.json, or any seed central_seed.py built): the central world
model (central.py), brokers off, derived events (events.py) applied to every world each tick, and also:
GET  /forecast   ?naptan=<id>&horizon=<min 0-60> (station) | ?hub=<id>&horizon= | (none) = every station and hub
GET  /segments   ?horizon=<min>: price-weighted people per street edge, in /geometry order
GET  /cams       ?horizon=<min>: every camera's forecast count (mean, p10, p90)
POST /intervene  {kind: closure|capacity|demand_surge|demand_drop|weather, where, start?, end?, magnitude?,
                  shape?, note?, mode: "all"|"fork", weight?}; the old {kind, zoneId, atMin} shape still works
POST /scenario   {interventions: [<intervene bodies>], horizons?: [15, 60]}: what-if on copies of the live worlds,
                  {before: {stations, hubs}, after: {stations, hubs}, ms}; live state unchanged
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import brokers
import central
import engine
import feeds
import fixture
from worlds import seedmod

PORT = int(os.environ.get("MULTIVERSE_PORT", "8049"))
ALLOWED_ORIGIN = os.environ.get("MULTIVERSE_CORS_ORIGIN", "http://localhost:3047")
TWIN_URL = os.environ.get("TWIN_URL", "http://localhost:3047")
LIVE_ORIGIN = (51.5152, -0.1419)
MAX_N = 200
HERE = Path(__file__).resolve().parent
REAL_SEED = HERE / "seeds" / "oxford-circus.json"
LIVE_POLL_S = 60


class App:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.mv: engine.Multiverse | None = None
        self.mode = "replay"
        self.frames: list[dict] = []
        self.cursor = 0
        self.raw_root: Path | None = None
        self.last_live_ts = 0
        self.bikes_base: Any = None
        self.live_gen = 0
        self.event_feed: central.EventFeed | None = None
        self.events_on = True
        self.user_ivs: list[dict] = []
        self.event_ivs: list[dict] = []
        self.events_shown: list[dict] = []
        self.seen_stations: dict[str, str] = {}

    def replay_seed(self) -> tuple[dict, Path]:
        """MULTIVERSE_SEED if set; else the real Oxford Circus seed on the recorder's data when any `jamcams`
        is recorded (feeds.real_root); else the synthetic fixture."""
        seed_path = os.environ.get("MULTIVERSE_SEED")
        if seed_path:
            path = Path(seed_path)
            if not path.is_file() and (HERE / seed_path).is_file():
                path = HERE / seed_path
            raw = Path(os.environ.get("MULTIVERSE_RAW") or feeds.alt_root())
            return json.loads(path.read_text()), raw
        root = feeds.real_root()
        if root is not None and REAL_SEED.is_file():
            return json.loads(REAL_SEED.read_text()), root
        if not fixture.SEED_PATH.is_file():
            fixture.main()
        return json.loads(fixture.SEED_PATH.read_text()), Path(os.environ.get("MULTIVERSE_RAW") or fixture.RAW_DIR)

    @staticmethod
    def is_central(seed: dict) -> bool:
        return seed.get("model") == "central"

    def sync_events(self, ts: int) -> None:
        """Derived events at ts (events.derive via EventFeed) plus the user's placed interventions become the
        set every world carries; worlds re-run only when that set changed."""
        mv = self.need()
        if not self.is_central(mv.seed):
            return
        derived = self.event_feed.at(ts) if self.event_feed and self.events_on else []
        self.event_ivs, self.events_shown = central.resolve_events(derived, mv.seed, ts)
        mv.set_applied(self.user_ivs + self.event_ivs, "events")

    def spawn_central(self, body: dict, mode: str, n: int, horizon: int) -> dict:
        seed, self.raw_root = self.replay_seed()
        self.user_ivs, self.event_ivs, self.events_shown = [], [], []
        self.events_on = body.get("events", "on") in ("on", True)
        if mode == "replay":
            cams, _ = central.seed_ids(seed)
            if body.get("from"):
                start = int(body["from"])
                end = int(body.get("to") or start + feeds.DEFAULT_WINDOW_MIN * 60_000)
            else:
                window = feeds.default_window(self.raw_root, cams, int(time.time() * 1000))
                if window is None:
                    raise LookupError(f"no recorded jamcams under {self.raw_root}")
                start, end = window
            seed, self.seen_stations = central.seed_at(seed, self.raw_root, start)
            self.frames = central.load_frames(self.raw_root, seed, start + 1, end)
            self.event_feed = central.EventFeed(self.raw_root, start, end)
        else:
            now = int(time.time() * 1000)
            recs = feeds.latest_records(self.raw_root, "jamcams", now, k=1, back_min=5)
            if not recs:
                raise LookupError(f"no jamcams snapshot in the last 5 min under {self.raw_root}: is the recorder running?")
            seed, self.seen_stations = central.seed_at(seed, self.raw_root, recs[-1]["tsMs"])
            self.frames, self.last_live_ts = [], recs[-1]["tsMs"]
            self.event_feed = central.EventFeed(self.raw_root)
        self.mode, self.cursor = mode, 0
        mv = engine.Multiverse(seed, n=n, horizon=horizon, crn_seed=int(body.get("crnSeed") or 47),
                               rng_seed=int(body.get("rngSeed") or 7), model=central)
        mv.spawn()
        self.mv = mv
        self.sync_events(seed["at"])
        if mode == "live":
            threading.Thread(target=self.poll_live, args=(self.live_gen,), daemon=True).start()
        return self.state()

    @staticmethod
    def is_real(seed: dict) -> bool:
        return str(seed.get("source", "")).startswith("real")

    @staticmethod
    def seed_ids(seed: dict) -> tuple[set[str], set[str]]:
        return {c["id"] for c in seed.get("cams", [])}, {b["naptanId"] for b in seed.get("buses", []) if b.get("naptanId")}

    def real_frames(self, seed: dict, body: dict) -> list[dict]:
        """Recorded frames for the real seed: body {from, to} (ms), else the 45-min window with most YOLO counts."""
        cams, naptans = self.seed_ids(seed)
        if body.get("from"):
            start = int(body["from"])
            end = int(body.get("to") or start + feeds.DEFAULT_WINDOW_MIN * 60_000)
        else:
            window = feeds.default_window(self.raw_root, cams, int(time.time() * 1000))
            if window is None:
                raise LookupError(f"no recorded jamcams under {self.raw_root}")
            start, end = window
        return feeds.load_real_frames(self.raw_root, cams, start, end, naptans, with_feeds=True)

    def poll_live(self, gen: int) -> None:
        """Live + real seed: read the newest recorder snapshot every LIVE_POLL_S until the next spawn."""
        while True:
            time.sleep(LIVE_POLL_S)
            with self.lock:
                if gen != self.live_gen:
                    return
                try:
                    self.observe({})
                except Exception as exc:  # noqa: BLE001 - a bad tick must not kill the poller
                    print(f"live poll failed: {exc}", file=sys.stderr)

    def spawn(self, body: dict) -> dict:
        n = max(2, min(int(body.get("n") or engine.DEFAULT_N), MAX_N))
        horizon = max(1, min(int(body.get("horizonMin") or engine.DEFAULT_HORIZON), 120))
        mode = body.get("mode") or "replay"
        if mode not in ("replay", "live"):
            raise ValueError("mode must be replay or live")
        self.live_gen += 1
        self.last_live_ts = 0
        if self.is_central(self.replay_seed()[0]):
            if body.get("brokers") in ("on", True):
                raise ValueError("brokers read Oxford Circus feeds only; the central model runs brokers off")
            return self.spawn_central(body, mode, n, horizon)
        if mode == "replay":
            seed, self.raw_root = self.replay_seed()
            self.frames = self.real_frames(seed, body) if self.is_real(seed) else feeds.load_frames(self.raw_root)
            if self.frames and not (seed["at"] <= self.frames[0]["ts"] <= seed["at"] + horizon * 60_000):
                seed = engine.reseed(seed, self.frames[0]["ts"], self.frames[0]["readings"])
                self.frames = self.frames[1:]
        elif self.is_real((real := self.replay_seed())[0]):
            seed, self.raw_root = real
            cams, naptans = self.seed_ids(seed)
            now = int(time.time() * 1000)
            self.bikes_base = feeds.bikes_base(self.raw_root, now)
            frame = feeds.latest_real_frame(self.raw_root, cams, naptans, now, self.bikes_base)
            if frame is None:
                raise LookupError(f"no jamcams snapshot in the last 5 min under {self.raw_root}: is the recorder running?")
            seed = engine.reseed(seed, frame["ts"], frame["readings"])
            self.frames, self.last_live_ts = [], frame["ts"]
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
        mv.feeds = feeds.feeds_at(self.raw_root if self.raw_root else feeds.alt_root(), seed["at"])
        mv.spawn()
        self.mv = mv
        if mode == "live" and self.is_real(seed):
            threading.Thread(target=self.poll_live, args=(self.live_gen,), daemon=True).start()
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
                     "busDelayMin": body.get("busDelayMin"), **{k: body.get(k) for k in engine.EXTRA_KEYS}}
        elif self.mode == "live" and self.is_central(mv.seed):
            frame = central.latest_frame(self.raw_root, mv.seed, int(time.time() * 1000), self.seen_stations)
            if frame is None or frame["ts"] <= self.last_live_ts:
                return {"stale": True, "observation": None, "latestTs": frame["ts"] if frame else None, **self.state()}
            self.last_live_ts = frame["ts"]
        elif self.mode == "live" and self.is_real(mv.seed):
            cams, naptans = self.seed_ids(mv.seed)
            frame = feeds.latest_real_frame(self.raw_root, cams, naptans, int(time.time() * 1000), self.bikes_base)
            if frame is None or frame["ts"] <= self.last_live_ts:
                return {"stale": True, "observation": None, "latestTs": frame["ts"] if frame else None, **self.state()}
            self.last_live_ts = frame["ts"]
        elif self.mode == "live":
            frame = feeds.live_frame(TWIN_URL, list(mv.cam_names))
            frame["ts"] = frame["ts"] or mv.seed["at"]
            frame["feeds"] = feeds.feeds_at(feeds.alt_root(), frame["ts"])
        else:
            raise ValueError("replay mode: send {next: true} or {ts, readings}")
        if self.is_central(mv.seed):
            self.sync_events(frame["ts"])
        obs = mv.advance(frame)
        return {"observation": obs, "remaining": len(self.frames) - self.cursor, **self.state()}

    def intervene(self, body: dict) -> dict:
        mv = self.need()
        if self.is_central(mv.seed):
            return self.intervene_central(mv, body)
        zone = body.get("zoneId") or next((z["id"] for z in mv.seed["zones"] if z["name"] == body.get("zoneName")), None)
        forked = mv.intervene(str(body.get("kind") or "incident"), str(zone), float(body.get("atMin") or mv.now_minute()),
                              body.get("note"), float(body.get("prior") or engine.FORK_PRIOR))
        return {"forked": forked, **self.state()}

    def intervene_central(self, mv: engine.Multiverse, body: dict) -> dict:
        """The general intervention API. The old {kind, zoneId, atMin} shape maps onto it: closure -> closure,
        incident -> capacity 0.4, event -> demand_surge 0.5, weather -> weather, all around the zone's station."""
        if "where" not in body and (body.get("zoneId") or body.get("zoneName")):
            z = next((z for z in mv.seed["zones"] if body.get("zoneId") == z["id"] or body.get("zoneName") == z["name"]), None)
            if z is None:
                raise ValueError(f"unknown zone {body.get('zoneId') or body.get('zoneName')}")
            legacy = {"closure": ("closure", None, 80), "incident": ("capacity", 0.4, 120),
                      "event": ("demand_surge", 0.5, 300), "weather": ("weather", None, 0)}
            kind, mag, radius = legacy.get(str(body.get("kind") or "incident"), (None, None, 0))
            if kind is None:
                raise ValueError(f"unknown intervention kind {body.get('kind')}")
            lat, lon = next(((s["lat"], s["lon"]) for s in mv.seed["stations"] if s["naptan"] == z.get("naptan")), (None, None))
            body = {"kind": kind, "where": {"lat": lat, "lon": lon, "radiusM": radius} if kind != "weather" else None,
                    "start": float(body.get("atMin") or 0) - mv.now_minute(), "magnitude": mag, "note": body.get("note"),
                    "mode": "fork", "weight": body.get("prior") or engine.FORK_PRIOR}
        now = mv.seed["at"] + mv.now_minute() * 60_000
        iv = central.normalise_intervention(body, mv.seed, now, f"user-{len(self.user_ivs) + len(mv.worlds)}")
        mode = body.get("mode") or "all"
        if mode == "all":
            self.user_ivs.append(iv)
            mv.set_applied(self.user_ivs + self.event_ivs, f"applied {iv['kind']}")
            ids = [w["id"] for w in mv.live()]
        else:
            ids = mv.apply(iv, "fork", float(body.get("weight") or engine.FORK_PRIOR))
        public = {k: iv[k] for k in ("id", "kind", "where", "startMs", "endMs", "magnitude", "note", "segments", "stations")}
        return {"intervention": public, "mode": mode, ("applied" if mode == "all" else "forked"): ids, **self.state()}

    def scenario(self, body: dict) -> dict:
        """POST /scenario: the interventions on copies of the live worlds; the market itself is not touched."""
        mv = self.need()
        if not self.is_central(mv.seed):
            raise LookupError("/scenario needs a central seed")
        raws = body.get("interventions")
        if not isinstance(raws, list) or not raws:
            raise ValueError("interventions must be a non-empty list of /intervene bodies")
        horizons = tuple(int(h) for h in body.get("horizons") or central.FORECAST_HORIZONS)
        if not horizons or any(not 0 <= h <= central.FORECAST_MIN for h in horizons):
            raise ValueError(f"horizons must be minutes in 0-{central.FORECAST_MIN}")
        t0 = time.perf_counter()
        now = mv.seed["at"] + mv.now_minute() * 60_000
        ivs = [central.normalise_intervention(r, mv.seed, now, f"scenario-{i}") for i, r in enumerate(raws)]
        out = central.scenario(mv, ivs, horizons)
        return out | {"ms": round((time.perf_counter() - t0) * 1000)}

    def forecast(self, q: dict) -> dict:
        mv = self.need()
        if not self.is_central(mv.seed):
            raise LookupError("/forecast needs a central seed (MULTIVERSE_SEED=seeds/central-london.json)")
        horizon = int(q.get("horizon", ["15"])[0])
        if not 0 <= horizon <= central.FORECAST_MIN:
            raise ValueError(f"horizon must be 0-{central.FORECAST_MIN} min")
        if "naptan" in q:
            nap = q["naptan"][0]
            if nap not in mv.station_names:
                raise ValueError(f"unknown station {nap}; stations: {sorted(mv.station_names)}")
            return central.station_forecast(mv, nap, horizon)
        if "hub" in q:
            hub = next((h for h in mv.seed["hubs"] if h["id"] == q["hub"][0]), None)
            if hub is None:
                raise ValueError(f"unknown hub {q['hub'][0]}")
            return central.hub_forecast(mv, hub, horizon)
        return central.forecasts(mv, (horizon,))

    def segments(self, q: dict) -> dict:
        mv = self.need()
        if not self.is_central(mv.seed):
            raise LookupError("/segments needs a central seed")
        return central.segment_forecast(mv, int(q.get("horizon", ["0"])[0]))

    def cams(self, q: dict) -> dict:
        mv = self.need()
        h = int(q.get("horizon", ["5"])[0])
        return {"horizonMin": h, "cams": central.cam_forecast(mv, h)}

    def state(self) -> dict:
        mv = self.need()
        extra = {}
        if self.is_central(mv.seed):
            extra = {"forecast": central.forecasts(mv), "events": self.events_shown,
                     "interventions": [{k: iv[k] for k in ("id", "kind", "note", "startMs", "endMs", "magnitude")}
                                       for iv in self.user_ivs],
                     "graph": {"edges": len(mv.seed["segments"]), "nodes": len(mv.seed.get("nodes", [])),
                               "bbox": mv.seed.get("bbox")}}
        return {**extra, "mode": self.mode, "real": self.is_real(mv.seed), "liveTs": self.last_live_ts or None,
                "replay": {"frames": len(self.frames), "cursor": self.cursor,
                           "nextTs": self.frames[self.cursor]["ts"] if self.cursor < len(self.frames) else None},
                **mv.state()}

    def replay(self, q: dict) -> dict:
        seed, default_root = self.replay_seed()
        root = self.raw_root or default_root
        start = int(q["from"][0]) if "from" in q else None
        end = int(q["to"][0]) if "to" in q else None
        if self.is_real(seed):
            cams, naptans = self.seed_ids(seed)
            now = int(time.time() * 1000)
            frames = feeds.load_real_frames(root, cams, start or now - 3_600_000, end or now, naptans)
        else:
            frames = feeds.load_frames(root, start, end)
        return {"root": str(root), "frames": [{"ts": f["ts"], "cams": len(f["readings"]), "busDelayMin": f["busDelayMin"],
                                               **{k: f.get(k) for k in engine.EXTRA_KEYS}} for f in frames]}

    def vix(self) -> dict:
        mv = self.need()
        return {"horizon_min": mv.horizon, **mv.vix_state(last=len(mv.vix_history))}

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
                "cams": [{k: c.get(k) for k in ("id", "name", "x", "z")} for c in mv.seed.get("cams", [])],
                **({"stations": [{k: s.get(k) for k in ("naptan", "name", "x", "z", "lat", "lon", "hub", "zoneId")}
                                  for s in mv.seed["stations"]],
                    "hubs": [{k: h[k] for k in ("id", "name", "naptan", "x", "z", "zoneIds")} for h in mv.seed["hubs"]],
                    "origin": mv.seed["origin"]} if self.is_central(mv.seed) else {})}


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
                  "/brokers": APP.brokers, "/vix": APP.vix, "/forecast": lambda: APP.forecast(q),
                  "/segments": lambda: APP.segments(q), "/cams": lambda: APP.cams(q)}
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
        routes = {"/spawn": APP.spawn, "/observe": APP.observe, "/intervene": APP.intervene, "/scenario": APP.scenario}
        if path in routes:
            self.handle_any(lambda: routes[path](body))
        else:
            self._send(404, {"error": "not found"})


def main() -> None:
    seed, root = APP.replay_seed()
    data = (f"REAL data: seed {seed.get('area')} ({seed.get('source')}), recorder {root}" if App.is_real(seed)
            else f"SYNTHETIC fixture (no recorded jamcams found), raw {root}")
    print(f"multiverse on :{PORT} (twin {TWIN_URL}); {data}", file=sys.stderr)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
