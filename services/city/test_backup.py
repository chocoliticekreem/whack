"""Ledger backups: a consistent copy, the rotation, and the optional Supabase upload.

    python3 -m unittest discover -s services/city
"""
from __future__ import annotations

import contextlib
import io
import sqlite3
import tempfile
import unittest
import urllib.error
from pathlib import Path

import backup
from ledger import GRANT, Ledger


class Backups(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.db = self.dir / "ledger.db"
        self.ledger = Ledger(self.db)
        self.addCleanup(self.ledger.close)
        self.out = self.dir / "backups"

    def test_copy_is_a_consistent_ledger_taken_while_it_is_open(self):
        a = self.ledger.create_player("Ann")
        self.ledger.set_pin(a["player"], "4821")
        copy = backup.backup_once(self.db, self.out, now=lambda: 1_700_000_000.5)
        self.assertEqual(copy.name, "ledger-20231114T221320500Z.db")
        self.assertEqual(list(self.out.glob("*.part")), [])
        db = sqlite3.connect(copy)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(db.execute("SELECT SUM(amount) FROM entries").fetchone()[0], GRANT)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM player_pins").fetchone()[0], 1)
        restored = Ledger(copy)
        self.addCleanup(restored.close)
        self.assertEqual(restored.recover("Ann", "4821")["player"], a["player"])
        self.assertEqual(restored.audit(), [])

    def test_keeps_only_the_newest(self):
        t = [1_700_000_000.0]
        made = []
        for _ in range(6):
            made.append(backup.backup_once(self.db, self.out, keep=4, now=lambda: t[0]).name)
            t[0] += 300
        self.assertEqual(sorted(p.name for p in self.out.glob("ledger-*.db")), made[-4:])

    def test_default_keeps_48(self):
        self.assertEqual((backup.KEEP, backup.INTERVAL_S, backup.BUCKET), (48, 300, "ledger-backups"))

    def test_missing_ledger_is_an_error_not_an_empty_copy(self):
        with self.assertRaises(FileNotFoundError):
            backup.backup_once(self.dir / "nope.db", self.out)
        self.assertFalse((self.dir / "nope.db").exists())

    def test_upload_is_skipped_without_supabase_env(self):
        copy = backup.backup_once(self.db, self.out)
        calls = []
        self.assertFalse(backup.upload(copy, env={}, opener=lambda *a, **k: calls.append(a)))
        self.assertFalse(backup.upload(copy, env={"SUPABASE_URL": "https://x.supabase.co"},
                                       opener=lambda *a, **k: calls.append(a)))
        self.assertEqual(calls, [])

    def test_upload_posts_to_the_bucket_and_never_logs_the_key(self):
        copy = backup.backup_once(self.db, self.out)
        env = {"SUPABASE_URL": "https://x.supabase.co/", "SUPABASE_SERVICE_ROLE_KEY": "sekrit-key"}
        seen = []

        class Ok:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(req, timeout):
            seen.append(req)
            return Ok()

        self.assertTrue(backup.upload(copy, env=env, opener=opener))
        req = seen[0]
        self.assertEqual(req.full_url, f"https://x.supabase.co/storage/v1/object/ledger-backups/{copy.name}")
        self.assertEqual(req.get_header("Authorization"), "Bearer sekrit-key")
        self.assertEqual(req.data, copy.read_bytes())

        def refuse(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertFalse(backup.upload(copy, env=env, opener=refuse))
        self.assertIn("HTTP 403", err.getvalue())
        self.assertNotIn("sekrit", err.getvalue())

    def test_memory_ledger_starts_no_thread(self):
        import threading
        before = threading.active_count()
        backup.start(":memory:")
        self.assertEqual(threading.active_count(), before)


if __name__ == "__main__":
    unittest.main()
