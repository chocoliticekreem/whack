"""Predict: stdlib HTTP server. POST /predict, GET /predict/{runId}, POST /score, GET /health.

Runs live in memory and as JSON under runs/ so a restart can still score an earlier forecast.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import llm
import personas
import report
import seed as seedmod
import simulate

PORT = int(os.environ.get("PREDICT_PORT", "8048"))
ALLOWED_ORIGIN = os.environ.get("PREDICT_CORS_ORIGIN", "http://localhost:3047")
RUNS_DIR = Path(__file__).parent / "runs"
RUNS: dict[str, dict] = {}
# The YOLO count is people in frame; the forecast is people per 100 m on the camera's street.
PEOPLE_PER_FRAME = seedmod.CAM_VIEW_M / 100.0
RIGHT_IF_WITHIN = 0.3
RIGHT_IF_ABS = 2.0
FLAT_PEOPLE = 1.0


def save_run(run: dict) -> None:
    RUNS[run["runId"]] = run
    RUNS_DIR.mkdir(exist_ok=True)
    (RUNS_DIR / f"{run['runId']}.json").write_text(json.dumps(run))


def load_run(run_id: str) -> dict | None:
    if run_id in RUNS:
        return RUNS[run_id]
    path = RUNS_DIR / f"{run_id}.json"
    if not path.is_file() or not run_id.replace("-", "").isalnum():
        return None
    RUNS[run_id] = json.loads(path.read_text())
    return RUNS[run_id]


def predict(body: dict) -> dict:
    horizon = int(body.get("horizonMin") or 30)
    horizon = max(5, min(horizon, 120))
    interventions = body.get("interventions") or []
    if not isinstance(interventions, list):
        raise ValueError("interventions must be a list")
    use_llm = bool(body.get("useLlm", True))
    rng_seed = int(body.get("rngSeed") or 47)
    origin = parse_origin(body.get("origin"))
    area = body.get("area") if isinstance(body.get("area"), str) else None
    seed = seedmod.build_seed(interventions, origin, area)
    groups, mode = personas.generate(seed, horizon, use_llm, __import__("random").Random(rng_seed))
    sim = simulate.run_simulation(seed, groups, horizon, use_llm, rng_seed)
    rep = report.build_report(seed, groups, sim, mode)
    run = {"runId": str(uuid.uuid4())[:8], "at": seed["at"], "horizonMin": horizon,
           "seed": seedmod.seed_summary(seed), "personas": groups, "timeline": sim["timeline"], "report": rep,
           "interventions": sim["interventions"], "camSegments": cam_segments(seed),
           "keyZoneSegments": key_zone_segments(seed, rep)}
    if mode == "llm" and llm.weave_project():
        run["trace"] = {"weave_project": llm.weave_project(), "model": llm.model_name()}
    save_run(run)
    return run


def parse_origin(raw: Any) -> tuple[float, float]:
    if not isinstance(raw, dict):
        return seedmod.ORIGIN
    try:
        lat, lon = float(raw["lat"]), float(raw["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("origin must be {lat, lon}") from exc
    if not (49.0 <= lat <= 61.0 and -11.0 <= lon <= 3.0):
        raise ValueError("origin must be in the UK")
    return (lat, lon)


def key_zone_segments(seed: dict, rep: dict) -> dict[str, list]:
    """The ghost layer draws only the report's zones, so it needs their segment ids and centres."""
    wanted = {it["zoneId"] for it in rep["items"]}
    out = {}
    for z in seed["zones"]:
        if z["id"] in wanted:
            out[z["id"]] = {"name": z["name"], "x": z["x"], "z": z["z"], "segmentIds": z["segmentIds"]}
    return out


def cam_segments(seed: dict) -> list[dict]:
    return [{"camId": s["camId"], "segmentId": s["id"], "zoneId": s.get("zoneId"), "seedPeds": s.get("peds", 0.0)}
            for s in seed["segments"] if s.get("camId")]


def score(body: dict) -> dict:
    run = load_run(str(body.get("runId", "")))
    if run is None:
        raise KeyError("unknown runId")
    now_ms = int(time.time() * 1000)
    due = [step for step in run["timeline"] if step["t"] <= now_ms]
    if not due:
        first = run["timeline"][0]["t"]
        return {"runId": run["runId"], "compared": 0, "error": f"no forecast step has arrived yet; first due at {first}"}
    step = due[-1]
    ids = ",".join(c["camId"] for c in run["camSegments"])
    readings = seedmod.fetch_json(f"/api/around/counts?ids={urllib.parse.quote(ids)}", 60.0).get("readings", [])
    actual = {r["camId"]: r for r in readings}
    predicted = {s["id"]: s["peds"] for s in step["segments"]}
    cams, by_zone = [], {}
    for cs in run["camSegments"]:
        r = actual.get(cs["camId"])
        if not r or r.get("at", 0) < step["t"] - 15 * 60_000:
            continue
        # Timeline segments list only changes vs now; an unlisted segment still holds its seed value.
        pred_people = predicted.get(cs["segmentId"], cs["seedPeds"]) * PEOPLE_PER_FRAME
        now_people = cs["seedPeds"] * PEOPLE_PER_FRAME
        cams.append({"camId": cs["camId"], "zoneId": cs["zoneId"], "predictedPeople": round(pred_people),
                     "nowPeople": round(now_people), "actualPeople": r["people"], "actualAt": r["at"]})
        agg = by_zone.setdefault(cs["zoneId"], {"zoneId": cs["zoneId"], "predicted": 0.0, "now": 0.0, "actual": 0})
        agg["predicted"] += pred_people
        agg["now"] += now_people
        agg["actual"] += r["people"]
    zones = [judge(z) for z in by_zone.values()]
    return {"runId": run["runId"], "forecastMinute": step["minute"], "forecastT": step["t"], "comparedAt": now_ms,
            "compared": len(cams), "cams": cams, "zones": zones,
            "summary": {"zonesRight": sum(1 for z in zones if z["verdict"] == "right"),
                        "zonesWrong": sum(1 for z in zones if z["verdict"] == "wrong"),
                        "meanAbsError": round(sum(abs(z["predicted"] - z["actual"]) for z in zones) / len(zones), 2) if zones else None}}


def direction(delta: float) -> str:
    if delta > FLAT_PEOPLE:
        return "busier"
    if delta < -FLAT_PEOPLE:
        return "quieter"
    return "flat"


def judge(z: dict) -> dict:
    """Verdict by direction vs now (busier / quieter / flat); the size error is reported separately."""
    predicted_dir = direction(z["predicted"] - z["now"])
    actual_dir = direction(z["actual"] - z["now"])
    err = abs(z["predicted"] - z["actual"])
    pct = round(100.0 * err / z["actual"], 1) if z["actual"] else None
    right = predicted_dir == actual_dir
    size_ok = err <= RIGHT_IF_ABS or (pct is not None and pct <= RIGHT_IF_WITHIN * 100)
    verdict_text = f"direction {'right' if right else 'wrong'}"
    verdict_text += " · size close" if size_ok else (f" · size off by {pct:.0f} %" if pct is not None else f" · off by {err:.0f}")
    return {**z, "predicted": round(z["predicted"]), "now": round(z["now"]), "errorPct": pct,
            "predictedDirection": predicted_dir, "actualDirection": actual_dir,
            "verdict": "right" if right else "wrong", "verdictText": verdict_text}


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

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        if path == "/health":
            self._send(200, {"ok": True, "llm": llm.enabled(), "weave": llm.weave_project() is not None,
                             "model": llm.model_name() if llm.enabled() else None, "runs": len(RUNS)})
        elif path.startswith("/predict/"):
            run = load_run(path.split("/", 2)[2])
            self._send(200, run) if run else self._send(404, {"error": "unknown runId"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
            if path == "/predict":
                self._send(200, predict(body))
            elif path == "/score":
                self._send(200, score(body))
            else:
                self._send(404, {"error": "not found"})
        except KeyError as exc:
            self._send(404, {"error": str(exc).strip("'")})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:
            self._send(502, {"error": f"{type(exc).__name__}: {exc}"})


def main() -> None:
    llm.init_weave()
    print(f"predict on :{PORT} (twin {seedmod.TWIN_URL}, llm {'on' if llm.enabled() else 'off'})", file=sys.stderr)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
