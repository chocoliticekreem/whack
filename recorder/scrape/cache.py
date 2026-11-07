"""Polite GET for scrapers: on-disk cache (one fetch per URL per 10 min) and a gap between calls to a host."""

import hashlib
import threading
import time
import urllib.parse

from common import DATA, http

CACHE = DATA / "scrape_cache"
TTL = 600
MIN_GAP = 5.0  # TheSportsDB's free tier returned 429 at a 2.5 s gap; 5 s = 16 calls in 80 s

_last_call: dict[str, float] = {}
_lock = threading.Lock()


def cached_get(url: str, ttl: float = TTL, timeout: float = 30) -> bytes:
    path = CACHE / (hashlib.sha1(url.encode()).hexdigest() + ".bin")
    if path.is_file() and time.time() - path.stat().st_mtime < ttl:
        return path.read_bytes()
    host = urllib.parse.urlsplit(url).hostname or ""
    with _lock:
        wait = _last_call.get(host, 0) + MIN_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.time()
    body = http(url, timeout)[2]
    if not body.strip():
        raise ValueError(f"empty body from {host} (rate limited?)")
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return body
