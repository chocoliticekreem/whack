"""YOLO counts for TfL JamCams.

GET /counts?ids=JamCams_00001.03500,JamCams_00001.03506
    -> {"readings": [{"camId", "people", "vehicles", "at", "frame"}]}
GET /tracks?ids=...  -> {"clips": [{"camId", "at", "duration", "tracks": [{"id", "kind", "cls", "color", "pts": [[t, u, v]]}]}]}
    u, v = bottom-centre of the box, normalised to the frame (0..1).
    cls = YOLO class (person, car, bus, truck, motorcycle, bicycle); color = the object's median colour
    as "#rrggbb" (torso for people, body panel for vehicles), so the twin can draw it like the real one.
GET /frame?id=JamCams_00001.03500  -> annotated JPEG of the busiest analysed frame
GET /clip?id=JamCams_00001.03500   -> the exact MP4 the tracks came from (TfL overwrites its URL every few
    minutes, so playing TfL's URL directly can show a different 10 s than the one that was tracked)

Each clip is ~10s and TfL refreshes it every few minutes, so readings are cached per camera.

Virtual live cams (ids VCAM_*, apps/stream/src/lib/vcams.json) are pre-recorded 1080p street footage tracked
ahead of time by services/vision/vcams.py: /tracks serves those tracks, /counts what is on screen at the
wall-clock moment the player shows, /frame their busiest annotated frame.

Follower detection for the streamer's own camera (see README "Follower detection"):
POST /follow/frame?session=abc&t=<ms>[&lat=&lon=]  body: JPEG  -> same shape as /follow/state
GET  /follow/state?session=abc -> {"people": [{id, visible_s, appearances, moved_m, follower, last_box}], "followers": n}
"""

import base64
import json
import math
import os
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np
from ultralytics import YOLO
from ultralytics.trackers import BYTETracker
from ultralytics.utils import YAML, IterableSimpleNamespace
from ultralytics.utils.checks import check_yaml

PORT = int(os.environ.get("PORT", "8047"))
TTL_S = 240  # TfL replaces each clip every ~3-5 minutes
MAX_IDS = 12
STRIDE = 3  # analyse every 3rd frame (~8 fps): enough for ByteTrack at street speeds, a third of the cost
# JamCam frames are 352x288, so people are only a few pixels tall: upscale to 960 and use a stronger
# model than nano. YOLO26s at 960 finds ~17% more objects per frame than yolo11n at 640 on these clips.
WEIGHTS = os.environ.get("JAMCAM_YOLO_WEIGHTS", "yolo26s.pt")
IMGSZ = int(os.environ.get("JAMCAM_YOLO_IMGSZ", "960"))
CONF = 0.2
MIN_TRACK_POINTS = 4
PEOPLE = {0}
VEHICLES = {1, 2, 3, 5, 7}  # bicycle, car, motorcycle, bus, truck
CLASS_NAMES = {0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
# Part of the box to sample for colour, as (x0, x1, y0, y1) fractions: the torso, or the lower body panel (below the windows).
SAMPLE = {"person": (0.3, 0.7, 0.2, 0.45), "vehicle": (0.25, 0.75, 0.5, 0.8)}

# Virtual live cams (services/vision/vcams.py): 15 min of 1080p fixed street footage each, tracked ahead of
# time. The player starts them at wall-clock time mod duration, so "now" in the footage is the same everywhere.
APP = Path(__file__).resolve().parents[2] / "apps" / "stream"
VCAM_DIR = APP / "public" / "footage" / "cams"
try:
    VCAM_FILES = {c["id"]: c["file"] for c in json.loads((APP / "src" / "lib" / "vcams.json").read_text())}
except FileNotFoundError:
    VCAM_FILES = {}
CAM_PREFIXES = ("JamCams_", "VCAM_")

model = YOLO(WEIGHTS)
model_lock = threading.Lock()
cache: dict[str, dict] = {}
frames: dict[str, bytes] = {}
clip_bytes: dict[str, bytes] = {}
track_cache: dict[str, dict] = {}
inflight: set[str] = set()
state_lock = threading.Lock()
pool = ThreadPoolExecutor(max_workers=2)

# --- Follower detection -------------------------------------------------------------------------
# A follower is someone who stays in the streamer's own camera for a long time AND either keeps
# coming back after dropping out of frame or is still there after the streamer has walked away.
# 90 s: a bystander at a crossing or a shop front is gone well before that. Overridable via env so
# a short test clip can trip the flag.
FOLLOW_MIN_VISIBLE_S = float(os.environ.get("FOLLOW_MIN_S", "90"))
# 3 re-entries: one re-entry is normal on a busy pavement; three means they keep catching up.
FOLLOW_MIN_APPEARANCES = int(os.environ.get("FOLLOW_MIN_APPEARANCES", "3"))
# 150 m: roughly two London blocks; a stranger who is still in frame after that is walking with you.
FOLLOW_MIN_MOVED_M = float(os.environ.get("FOLLOW_MIN_MOVED_M", "150"))
# A gap this long between sightings counts as a separate appearance (they left the frame and came back).
REENTRY_GAP_S = 3.0
# A dossier not seen for this long is forgotten: re-identifying across longer gaps on colour alone is guesswork.
DOSSIER_TTL_S = 600
SESSION_IDLE_S = 600
# Bhattacharyya distance on the upper/lower HSV histograms; 0 is identical. Below this = same clothes.
REID_MAX_DIST = 0.32
HIST_EMA = 0.3  # weight of the newest crop when smoothing a dossier's appearance
FOLLOW_CONF = 0.35
FOLLOW_MIN_BOX_PX = 40  # tiny far-away boxes give noisy histograms
# Own model instance: the clip pipeline holds model_lock for a whole clip (10-20 s), which would
# stall live frames behind every "Around me" refresh. A second YOLO11n costs ~6 MB of weights.
follow_model = YOLO("yolo11n.pt")
follow_model_lock = threading.Lock()


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def appearance(frame: np.ndarray, box: list[float]) -> np.ndarray | None:
    """Colour signature of one person: HSV histograms of the upper and lower body halves (clothes, not face)."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = (max(0, int(box[0])), max(0, int(box[1])), min(w, int(box[2])), min(h, int(box[3])))
    if x2 - x1 < FOLLOW_MIN_BOX_PX // 2 or y2 - y1 < FOLLOW_MIN_BOX_PX:
        return None
    crop = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    mid = crop.shape[0] // 2
    parts = []
    for half in (crop[:mid], crop[mid:]):
        hist = cv2.calcHist([half], [0, 1], None, [12, 4], [0, 180, 0, 256])
        parts.append(cv2.normalize(hist, None, alpha=1, norm_type=cv2.NORM_L1).flatten())
    return np.concatenate(parts).astype(np.float32)


def hist_distance(a: np.ndarray, b: np.ndarray) -> float:
    half = len(a) // 2
    d_up = cv2.compareHist(a[:half], b[:half], cv2.HISTCMP_BHATTACHARYYA)
    d_low = cv2.compareHist(a[half:], b[half:], cv2.HISTCMP_BHATTACHARYYA)
    return float((d_up + d_low) / 2)


class FollowSession:
    def __init__(self) -> None:
        cfg = IterableSimpleNamespace(**YAML.load(check_yaml("bytetrack.yaml")))
        self.tracker = BYTETracker(cfg)
        self.dossiers: dict[int, dict] = {}
        self.track_to_dossier: dict[int, int] = {}
        self.next_id = 1
        self.touched = time.time()
        self.lock = threading.Lock()

    def _new_dossier(self, t: float, hist: np.ndarray | None, lat: float | None, lon: float | None) -> dict:
        d = {
            "id": self.next_id, "first_seen": t, "last_seen": t, "visible_s": 0.0, "appearances": 1,
            "hist": hist, "origin": (lat, lon) if lat is not None and lon is not None else None,
            "moved_m": 0.0, "last_box": None, "thumb": None,
        }
        self.next_id += 1
        self.dossiers[d["id"]] = d
        return d

    def _reidentify(self, hist: np.ndarray | None, seen_now: set[int], t: float) -> dict | None:
        if hist is None:
            return None
        best, best_d = None, REID_MAX_DIST
        for d in self.dossiers.values():
            if d["id"] in seen_now or d["hist"] is None or t - d["last_seen"] > DOSSIER_TTL_S:
                continue
            dist = hist_distance(hist, d["hist"])
            if dist < best_d:
                best, best_d = d, dist
        return best

    def ingest(self, frame: np.ndarray, t: float, lat: float | None, lon: float | None) -> dict:
        with follow_model_lock:
            boxes = follow_model.predict(frame, imgsz=640, conf=FOLLOW_CONF, classes=[0], verbose=False)[0].boxes.cpu().numpy()
        with self.lock:
            self.touched = time.time()
            tracks = self.tracker.update(boxes, frame) if len(boxes) else np.empty((0, 8))
            seen_now: set[int] = set()
            for x1, y1, x2, y2, tid, _conf, _cls, _idx in tracks.tolist():
                tid = int(tid)
                box = [x1, y1, x2, y2]
                hist = appearance(frame, box)
                d = self.dossiers.get(self.track_to_dossier.get(tid, -1))
                if d is None:
                    d = self._reidentify(hist, seen_now, t)
                    if d is None:
                        d = self._new_dossier(t, hist, lat, lon)
                        d["last_box"] = [round(v) for v in box]
                        d["thumb"] = thumbnail(frame, box)
                        seen_now.add(d["id"])
                        self.track_to_dossier[tid] = d["id"]
                        continue
                    self.track_to_dossier[tid] = d["id"]
                if d["id"] in seen_now:
                    continue  # two tracks resolved to one person this frame; count time once
                seen_now.add(d["id"])
                gap = t - d["last_seen"]
                if gap > REENTRY_GAP_S:
                    d["appearances"] += 1
                else:
                    d["visible_s"] += max(0.0, gap)
                d["last_seen"] = t
                d["last_box"] = [round(v) for v in box]
                if hist is not None:
                    d["hist"] = hist if d["hist"] is None else (1 - HIST_EMA) * d["hist"] + HIST_EMA * hist
                if d["origin"] and lat is not None and lon is not None:
                    d["moved_m"] = max(d["moved_m"], haversine_m(d["origin"][0], d["origin"][1], lat, lon))
                if d["thumb"] is None:
                    d["thumb"] = thumbnail(frame, box)
            # Forget people not seen for a long time (and the track ids that pointed at them).
            stale = [i for i, d in self.dossiers.items() if t - d["last_seen"] > DOSSIER_TTL_S]
            for i in stale:
                del self.dossiers[i]
            self.track_to_dossier = {k: v for k, v in self.track_to_dossier.items() if v in self.dossiers}
            return self.state_locked(t)

    def state(self) -> dict:
        with self.lock:
            return self.state_locked(None)

    def state_locked(self, now: float | None) -> dict:
        people = []
        for d in self.dossiers.values():
            visible = d["visible_s"]
            follower = visible >= FOLLOW_MIN_VISIBLE_S and (
                d["appearances"] >= FOLLOW_MIN_APPEARANCES or d["moved_m"] >= FOLLOW_MIN_MOVED_M
            )
            people.append({
                "id": d["id"], "visible_s": round(visible, 1), "appearances": d["appearances"],
                "moved_m": round(d["moved_m"]), "follower": follower, "last_box": d["last_box"],
                "last_seen_s_ago": None if now is None else round(now - d["last_seen"], 1),
                **({"thumb": d["thumb"]} if d["thumb"] else {}),
            })
        people.sort(key=lambda p: (-p["follower"], -p["visible_s"]))
        return {"people": people, "followers": sum(p["follower"] for p in people)}


def thumbnail(frame: np.ndarray, box: list[float]) -> str | None:
    """Tiny, heavily downscaled torso crop (top third skipped) so the card can show "this outfit", never a face."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = max(0, int(box[0])), max(0, int(box[1])), min(w, int(box[2])), min(h, int(box[3]))
    y1 += (y2 - y1) // 3
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    crop = cv2.resize(frame[y1:y2, x1:x2], (24, 32), interpolation=cv2.INTER_AREA)
    ok, jpg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 60])
    return "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode() if ok else None


sessions: dict[str, FollowSession] = {}
sessions_lock = threading.Lock()


def get_session(sid: str, create: bool) -> FollowSession | None:
    now = time.time()
    with sessions_lock:
        for key in [k for k, s in sessions.items() if now - s.touched > SESSION_IDLE_S]:
            del sessions[key]
        s = sessions.get(sid)
        if s is None and create:
            s = sessions[sid] = FollowSession()
        return s


def clip_url(cam_id: str) -> str:
    return f"https://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/{cam_id.removeprefix('JamCams_')}.mp4"


def sample_color(frame, kind: str, x1: float, y1: float, x2: float, y2: float):
    """Median BGR of the sampled part of the box, or None when the box is too small to trust."""
    fx0, fx1, fy0, fy1 = SAMPLE[kind]
    w, h = x2 - x1, y2 - y1
    xa, xb = int(x1 + w * fx0), int(x1 + w * fx1)
    ya, yb = int(y1 + h * fy0), int(y1 + h * fy1)
    patch = frame[max(ya, 0):yb, max(xa, 0):xb]
    if patch.size < 3 * 4:
        return None
    return np.median(patch.reshape(-1, 3), axis=0)


def finish_track(t: dict) -> dict:
    """Collapse per-frame samples into one class (majority vote) and one colour (median)."""
    classes = t.pop("classes")
    colors = t.pop("colors")
    sizes = t.pop("sizes")
    t["cls"] = max(set(classes), key=classes.count)
    if colors:
        b, g, r = (int(c) for c in np.median(np.array(colors), axis=0))
        t["color"] = f"#{r:02x}{g:02x}{b:02x}"
    if sizes:
        # Median box size (frame fractions); the twin checks the class against it (YOLO calls a red SUV a bus).
        t["box"] = [round(float(x), 4) for x in np.median(np.array(sizes), axis=0)]
    return t


def track_clip(path: str):
    """Track people and vehicles through one clip. Returns counts, tracks and the busiest annotated frame."""
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    tracks: dict[int, dict] = {}
    people = vehicles = 0
    best_plot, best_total, idx = None, -1, 0
    # One lock for the whole clip: the tracker keeps state between frames, so clips can't interleave.
    with model_lock:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % STRIDE == 0:
                h, w = frame.shape[:2]
                # Class-agnostic NMS: one van seen as both "car" and "truck" is one box, not two overlapping tracks.
                result = model.track(
                    frame, imgsz=IMGSZ, conf=CONF, persist=idx > 0, tracker="bytetrack.yaml",
                    classes=sorted(PEOPLE | VEHICLES), agnostic_nms=True, verbose=False,
                )[0]
                boxes = result.boxes
                classes = [int(c) for c in boxes.cls.tolist()]
                p = sum(c in PEOPLE for c in classes)
                v = sum(c in VEHICLES for c in classes)
                # Peak frame, not the average: a crowd crossing mid-clip is what matters.
                if p + v > best_total:
                    best_total, best_plot = p + v, result.plot(line_width=1, labels=False, conf=False)
                people, vehicles = max(people, p), max(vehicles, v)
                if boxes.id is not None:
                    for tid, c, (x1, y1, x2, y2) in zip(boxes.id.int().tolist(), classes, boxes.xyxy.tolist()):
                        kind = "person" if c in PEOPLE else "vehicle"
                        t = tracks.setdefault(tid, {"id": tid, "kind": kind, "pts": [], "classes": [], "colors": [], "sizes": []})
                        # Bottom-centre of the box = where the object touches the ground.
                        t["pts"].append([round(idx / fps, 2), round((x1 + x2) / 2 / w, 4), round(y2 / h, 4)])
                        t["classes"].append(CLASS_NAMES.get(c, kind))
                        color = sample_color(frame, kind, x1, y1, x2, y2)
                        if color is not None:
                            t["colors"].append(color)
                        if x1 > 2 and y1 > 2 and x2 < w - 2 and y2 < h - 2:  # boxes cut by the frame edge would undersize it
                            t["sizes"].append(((x2 - x1) / w, (y2 - y1) / h))
            idx += 1
    cap.release()
    kept = [finish_track(t) for t in tracks.values() if len(t["pts"]) >= MIN_TRACK_POINTS]
    return people, vehicles, kept, best_plot, round(idx / fps, 2)


def analyse(cam_id: str) -> None:
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4") as tmp:
            req = urllib.request.Request(clip_url(cam_id), headers={"User-Agent": "classroom-47"})
            with urllib.request.urlopen(req, timeout=15) as res:
                mp4 = res.read()
            tmp.write(mp4)
            tmp.flush()
            people, vehicles, kept, best_plot, duration = track_clip(tmp.name)
        at = int(time.time() * 1000)
        reading = {"camId": cam_id, "people": people, "vehicles": vehicles, "at": at}
        with state_lock:
            cache[cam_id] = reading
            track_cache[cam_id] = {"camId": cam_id, "at": at, "duration": duration, "tracks": kept}
            clip_bytes[cam_id] = mp4
            if best_plot is not None:
                ok, jpg = cv2.imencode(".jpg", best_plot, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    frames[cam_id] = jpg.tobytes()
    except Exception as e:  # noqa: BLE001 - one bad clip must not kill the batch
        print(f"[yolo] {cam_id} failed: {e}", flush=True)
    finally:
        with state_lock:
            inflight.discard(cam_id)


vcam_cache: dict[str, dict] = {}


def vcam_clip(cam_id: str) -> dict | None:
    """A virtual cam's pre-computed tracks, in the same shape as a tracked JamCam clip."""
    if cam_id not in vcam_cache:
        path = VCAM_DIR / f"{VCAM_FILES.get(cam_id, '')}.tracks.json"
        if not VCAM_FILES.get(cam_id) or not path.is_file():
            return None
        vcam_cache[cam_id] = json.loads(path.read_text())
    return vcam_cache[cam_id]


def vcam_reading(cam_id: str) -> dict | None:
    """People and vehicles on screen in the moment of footage that is playing right now."""
    clip = vcam_clip(cam_id)
    if not clip or clip["duration"] <= 0:
        return None
    now = time.time()
    t = now % clip["duration"]
    live = [tr for tr in clip["tracks"] if tr["pts"][0][0] <= t + 1.5 and tr["pts"][-1][0] >= t - 1.5]
    return {
        "camId": cam_id,
        "people": sum(tr["kind"] == "person" for tr in live),
        "vehicles": sum(tr["kind"] == "vehicle" for tr in live),
        "at": int(now * 1000),
        "frame": (VCAM_DIR / f"{VCAM_FILES[cam_id]}.frame.jpg").is_file(),
    }


def schedule(cam_id: str) -> None:
    with state_lock:
        fresh = cam_id in cache and time.time() * 1000 - cache[cam_id]["at"] < TTL_S * 1000
        if fresh or cam_id in inflight:
            return
        inflight.add(cam_id)
    pool.submit(analyse, cam_id)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        # Node's fetch pools sockets; without these it can reuse one this HTTP/1.0 server already closed
        # and hang until its timeout, which showed up as 503s on ~5% of follower frames.
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == "/counts":
            ids = [i for i in q.get("ids", [""])[0].split(",") if i.startswith(CAM_PREFIXES)][:MAX_IDS]
            virtual = [i for i in ids if i.startswith("VCAM_")]
            ids = [i for i in ids if i not in virtual]
            for cam_id in ids:
                schedule(cam_id)
            with state_lock:
                readings = [
                    {**cache[i], "frame": i in frames} for i in ids if i in cache
                ]
                pending = [i for i in ids if i not in cache]
            readings += [r for r in map(vcam_reading, virtual) if r]
            self._send(200, json.dumps({"readings": readings, "pending": pending}).encode(), "application/json")
        elif url.path == "/tracks":
            ids = [i for i in q.get("ids", [""])[0].split(",") if i.startswith(CAM_PREFIXES)][:MAX_IDS]
            virtual = [i for i in ids if i.startswith("VCAM_")]
            ids = [i for i in ids if i not in virtual]
            for cam_id in ids:
                schedule(cam_id)
            with state_lock:
                clips = [track_cache[i] for i in ids if i in track_cache]
            clips += [c for c in map(vcam_clip, virtual) if c]
            self._send(200, json.dumps({"clips": clips}).encode(), "application/json")
        elif url.path == "/frame":
            cam_id = q.get("id", [""])[0]
            still = VCAM_DIR / f"{VCAM_FILES.get(cam_id, '')}.frame.jpg"
            with state_lock:
                jpg = still.read_bytes() if VCAM_FILES.get(cam_id) and still.is_file() else frames.get(cam_id)
            if jpg:
                self._send(200, jpg, "image/jpeg")
            else:
                self._send(404, b'{"error":"no frame yet"}', "application/json")
        elif url.path == "/follow/state":
            s = get_session(q.get("session", [""])[0], create=False)
            body = s.state() if s else {"people": [], "followers": 0}
            self._send(200, json.dumps(body).encode(), "application/json")
        elif url.path == "/clip":
            with state_lock:
                mp4 = clip_bytes.get(q.get("id", [""])[0])
            if mp4:
                self._send(200, mp4, "video/mp4")
            else:
                self._send(404, b'{"error":"no clip yet"}', "application/json")
        elif url.path == "/health":
            self._send(200, b'{"ok":true}', "application/json")
        else:
            self._send(404, b'{"error":"not found"}', "application/json")

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path != "/follow/frame":
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        sid = q.get("session", [""])[0]
        if not sid:
            self._send(400, b'{"error":"session required"}', "application/json")
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            self._send(400, b'{"error":"body must be a JPEG"}', "application/json")
            return
        t = float(q.get("t", [str(int(time.time() * 1000))])[0]) / 1000
        lat = float(q["lat"][0]) if "lat" in q else None
        lon = float(q["lon"][0]) if "lon" in q else None
        state = get_session(sid, create=True).ingest(frame, t, lat, lon)
        self._send(200, json.dumps(state).encode(), "application/json")

    def log_message(self, *_args) -> None:
        pass


if __name__ == "__main__":
    print(f"[yolo] listening on http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
