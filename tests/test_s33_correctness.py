import importlib
import logging
import os
import re
import sqlite3
import sys
import tempfile
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CorrectnessCleanupTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        os.environ["UPLIVION_UPLOAD_DIR"] = str(root / "share")
        os.environ["UPLIVION_DB_PATH"] = str(root / "store.db")
        os.environ["SECRET_KEY"] = "s" * 32
        os.environ["ACCESS_TOKEN_SECRET"] = "a" * 32
        sys.modules.pop("server", None)
        self.server = importlib.import_module("server")

    def tearDown(self):
        sys.modules.pop("server", None)
        self.temp_dir.cleanup()

    def test_database_session_commits_and_closes(self):
        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO users
                    (user_id, username, password_hash, quota_bytes, created)
                VALUES ('user-id', 'user', X'00', 1073741824, 'now')
                """
            )
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

        with self.server.db_session() as second:
            count = second.execute(
                "SELECT COUNT(*) FROM users WHERE user_id = 'user-id'"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_server_uses_timezone_aware_tokens_and_closing_sessions(self):
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("datetime.utcnow()", source)
        self.assertIn("datetime.now(timezone.utc)", source)
        self.assertNotRegex(
            source,
            r"with (?:db_write_lock,\s*)?db_connect\(\) as conn",
        )

    def test_reload_does_not_duplicate_console_handler(self):
        logger = logging.getLogger("server")
        before = [
            handler
            for handler in logger.handlers
            if getattr(handler, "_uplivion_console_handler", False)
        ]
        self.assertEqual(len(before), 1)

        sys.modules.pop("server", None)
        self.server = importlib.import_module("server")
        after = [
            handler
            for handler in logger.handlers
            if getattr(handler, "_uplivion_console_handler", False)
        ]
        self.assertEqual(after, before)

    def test_shared_error_page_is_status_neutral_and_zoomable(self):
        html = (ROOT / "public" / "error" / "error.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("<title>Request unavailable</title>", html)
        self.assertNotIn("404", html)
        self.assertNotIn("user-scalable=no", html)
        self.assertNotIn("maximum-scale=1", html)

    def test_cleanup_guards_remain_scoped(self):
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn("candidate.relative_to(upload_root)", source)
        self.assertNotIn("startswith(upload_dir_abs)", source)
        self.assertNotRegex(
            source,
            re.compile(
                r'DELETE FROM quota_reservations WHERE file_id = \?["\']'
            ),
        )

