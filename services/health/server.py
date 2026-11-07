"""GET /health on :8052 (stdlib): the health.py snapshot as JSON, for the city backend and the web app.

    python3 services/health/server.py         # HEALTH_PORT to change the port

The data scan is cached for 5 s, the YOLO ping for 10 s and the TfL key check for 5 min, so polling
/health every second costs nothing. CORS is open to any http://localhost or 127.0.0.1 origin.
"""

import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import health  # noqa: E402

PORT = int(os.environ.get("HEALTH_PORT", "8052"))
LOCAL_ORIGIN = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$")
SNAPSHOT_TTL, YOLO_TTL, TFL_TTL = 5, 10, 300


class Cache:
    def __init__(self):
        self.lock = threading.Lock()
        self.snap: dict | None = None
        self.snap_at = self.yolo_at = self.tfl_at = 0.0
        self.yolo_up = False
        self.tfl: dict = {}

    def get(self) -> dict:
        with self.lock:
            now = time.time()
            if now - self.tfl_at >= TFL_TTL:
                self.tfl, self.tfl_at = health.tfl_key_check(), now
            if now - self.yolo_at >= YOLO_TTL:
                url = os.environ.get("JAMCAM_YOLO_URL", "http://127.0.0.1:8047").rstrip("/")
                self.yolo_up, self.yolo_at = health.yolo_check(url), now
            if self.snap is None or now - self.snap_at >= SNAPSHOT_TTL:
                self.snap = health.snapshot(now=now, tfl=self.tfl, yolo_reachable=self.yolo_up)
                self.snap_at = now
            return self.snap


CACHE = Cache()


class Handler(BaseHTTPRequestHandler):
    def _cors(self) -> None:
        origin = self.headers.get("Origin", "")
        if LOCAL_ORIGIN.match(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib name
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        if self.path.split("?")[0] != "/health":
            self._send(404, {"error": "not found"})
            return
        try:
            self._send(200, CACHE.get())
        except Exception as e:  # noqa: BLE001 - a broken scan must read as "unknown", not a dropped connection
            self._send(500, {"status": "unknown", "error": f"{type(e).__name__}: {e}"})

    def log_message(self, fmt: str, *args) -> None:
        pass


def main() -> None:
    health.load_env()
    print(f"[health] serving http://127.0.0.1:{PORT}/health from {health.RAW}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
