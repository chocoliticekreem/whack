"""Ledger backups. Every INTERVAL_S a consistent copy of the ledger SQLite (the sqlite3 backup API, safe while the
server writes) goes to services/city/data/backups/, keeping the newest KEEP. With SUPABASE_URL and
SUPABASE_SERVICE_ROLE_KEY set, each new copy is also uploaded to the Supabase Storage bucket "ledger-backups".

    python3 services/city/backup.py              # one backup of CITY_LEDGER_DB (default data/ledger.db) now
"""
from __future__ import annotations

import os
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

INTERVAL_S = 300
KEEP = 48
BUCKET = "ledger-backups"
DEFAULT_DIR = Path(__file__).resolve().parent / "data" / "backups"


def backup_once(db_path: str | os.PathLike, dest: Path = DEFAULT_DIR, keep: int = KEEP,
                now: Callable[[], float] = time.time) -> Path:
    src_path = Path(db_path)
    if not src_path.is_file():
        raise FileNotFoundError(f"no ledger at {src_path}")
    dest.mkdir(parents=True, exist_ok=True)
    t = now()
    out = dest / f"ledger-{time.strftime('%Y%m%dT%H%M%S', time.gmtime(t))}{int(t * 1000) % 1000:03d}Z.db"
    part = out.with_name(out.name + ".part")
    src, dst = sqlite3.connect(src_path), sqlite3.connect(part)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.replace(part, out)
    for old in sorted(dest.glob("ledger-*.db"))[:-keep]:
        old.unlink()
    return out


def upload(path: Path, env: dict = os.environ, opener=urllib.request.urlopen) -> bool:
    """POST the file to Supabase Storage. False (and a log line without the key) on any failure."""
    base, key = env.get("SUPABASE_URL"), env.get("SUPABASE_SERVICE_ROLE_KEY")
    if not (base and key):
        return False
    req = urllib.request.Request(f"{base.rstrip('/')}/storage/v1/object/{BUCKET}/{path.name}", data=path.read_bytes(),
                                 method="POST", headers={"Authorization": f"Bearer {key}", "apikey": key,
                                                         "Content-Type": "application/octet-stream",
                                                         "x-upsert": "true"})
    try:
        with opener(req, timeout=30) as res:
            return 200 <= res.status < 300
    except urllib.error.HTTPError as exc:
        print(f"backup upload of {path.name} failed: HTTP {exc.code}", file=sys.stderr)
    except (urllib.error.URLError, OSError) as exc:
        print(f"backup upload of {path.name} failed: {type(exc).__name__}", file=sys.stderr)
    return False


def run(db_path: str, dest: Path = DEFAULT_DIR, interval_s: float = INTERVAL_S) -> None:
    while True:
        try:
            upload(backup_once(db_path, dest))
        except Exception as exc:  # noqa: BLE001 - a failed backup must never stop the next one
            print(f"ledger backup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        time.sleep(interval_s)


def start(db_path: str) -> None:
    """Background backups for a file ledger; an in-memory (replay) ledger has nothing to keep."""
    if db_path != ":memory:":
        threading.Thread(target=run, args=(db_path,), daemon=True, name="ledger-backup").start()


if __name__ == "__main__":
    from ledger import DEFAULT_DB
    copy = backup_once(os.environ.get("CITY_LEDGER_DB") or DEFAULT_DB)
    print(copy, "uploaded" if upload(copy) else "local only")
