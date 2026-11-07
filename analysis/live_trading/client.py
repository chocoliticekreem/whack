"""Stdlib HTTP client for the city markets API, with every call appended to a JSONL log.

One line per call: {"t", "actor", "method", "path", "body", "status", "ms", "resp"}. The log is the evidence the
README cites by line number.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRATCH = HERE / "scratch"


class Log:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.lock = path, threading.Lock()
        self.f = open(path, "a", buffering=1)
        self.n = sum(1 for _ in open(path)) if path.exists() else 0

    def write(self, rec: dict) -> int:
        with self.lock:
            self.n += 1
            rec = {"line": self.n, "t": int(time.time() * 1000)} | rec
            self.f.write(json.dumps(rec, default=str) + "\n")
            return self.n


class Api:
    def __init__(self, base: str, log: Log, timeout: float = 10.0):
        self.base, self.log, self.timeout = base.rstrip("/"), log, timeout

    def call(self, method: str, path: str, body: dict | None = None, token: str | None = None,
             actor: str = "-", quiet: bool = False, headers: dict | None = None) -> tuple[int, dict, int]:
        """(status, json, log line). Network errors come back as status 0 so a restart never kills a bot."""
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "overwatch-live-trading-test/1.0")
        if token:
            req.add_header("X-Player-Token", token)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            status, raw = 0, json.dumps({"netError": f"{type(e).__name__}: {e}"}).encode()
        ms = int((time.time() - t0) * 1000)
        try:
            resp = json.loads(raw or b"{}")
        except ValueError:
            resp = {"raw": raw[:500].decode("utf-8", "replace")}
        line = 0
        if not quiet:
            line = self.log.write({"actor": actor, "method": method, "path": path, "body": body, "status": status,
                                   "ms": ms, "resp": resp})
        return status, resp, line

    def get(self, path: str, q: dict | None = None, **kw):
        if q:
            path += "?" + urllib.parse.urlencode(q)
        return self.call("GET", path, **kw)

    def post(self, path: str, body: dict, **kw):
        return self.call("POST", path, body, **kw)


class Player:
    def __init__(self, api: Api, name: str, pid: str, token: str):
        self.api, self.name, self.id, self.token = api, name, pid, token

    @classmethod
    def new(cls, api: Api, name: str) -> "Player":
        st, r, _ = api.post("/player/new", {"name": name}, actor=name)
        if st != 200:
            raise RuntimeError(f"/player/new for {name} failed: {st} {r}")
        pid = r["player"]
        return cls(api, name, pid, r["token"])

    def get(self, path: str, q: dict | None = None, **kw):
        return self.api.get(path, {"player": self.id} | (q or {}), token=self.token, actor=self.name, **kw)

    def post(self, path: str, body: dict, **kw):
        return self.api.post(path, {"player": self.id} | body, token=self.token, actor=self.name, **kw)

    def bet(self, market: str, option: str, stake, key: str, **extra):
        return self.post("/bet", {"market": market, "option": option, "stake": stake, "idempotency_key": key} | extra)

    def json(self) -> dict:
        return {"name": self.name, "id": self.id, "token": self.token}
