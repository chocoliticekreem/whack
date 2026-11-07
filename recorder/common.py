"""Shared helpers: paths, .env loading, HTTP, gzip NDJSON writing."""

import gzip
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
# Wikimedia and adsb.lol ask for a UA that names the project and a contact.
USER_AGENT = "whack26-recorder/1.0 (WHACK 26 hackathon; +https://github.com/chocoliticekreem/whack)"


def _main_checkout_env() -> Path | None:
    """In a git worktree `.git` is a file pointing into the main checkout's .git/worktrees/."""
    dotgit = ROOT / ".git"
    if not dotgit.is_file():
        return None
    gitdir = dotgit.read_text().strip().removeprefix("gitdir:").strip()
    if "/.git/worktrees/" not in gitdir:
        return None
    return Path(gitdir.split("/.git/worktrees/")[0]) / ".env"


def load_env(env_file: str | None = None) -> None:
    candidates = [Path(env_file)] if env_file else [ROOT / ".env", _main_checkout_env()]
    env = next((p for p in candidates if p and p.is_file()), None)
    if env is None:
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class HttpError(Exception):
    def __init__(self, status: int, retry_after: float | None, msg: str, body: bytes = b""):
        super().__init__(msg)
        self.status = status
        self.retry_after = retry_after
        self.body = body


def http(url: str, timeout: float, method: str = "GET") -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as e:
        ra = e.headers.get("Retry-After") if e.headers else None
        try:
            retry_after = float(ra) if ra else None
        except ValueError:
            retry_after = None
        try:
            body = e.read(512)
        except Exception:  # noqa: BLE001 - the status code is the error; the body is a hint
            body = b""
        raise HttpError(e.code, retry_after, f"HTTP {e.code}", body) from None


def get_json(url: str, timeout: float = 30):
    return json.loads(http(url, timeout)[2])


_write_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    with _locks_guard:
        return _write_locks.setdefault(path, threading.Lock())


def write_record(source: str, ts: datetime, payload) -> int:
    """Append one record. Each write is its own gzip member, so a kill mid-run loses at most
    the line being written and every earlier line stays readable."""
    path = RAW / source / ts.strftime("%Y-%m-%d") / f"{ts.strftime('%H')}.ndjson.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"ts": iso(ts), "source": source, "payload": payload}, separators=(",", ":"))
    data = (line + "\n").encode()
    with _lock_for(path), gzip.open(path, "ab") as f:
        f.write(data)
    return len(data)


def sleep_until(deadline: float, stop: threading.Event) -> None:
    stop.wait(max(0.0, deadline - time.time()))
