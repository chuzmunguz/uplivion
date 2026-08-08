import bcrypt
import contextlib
import hashlib
import io
import sqlite3
import tempfile
import time
import unittest

from pathlib import Path
from unittest import mock

import create_users

from store_schema import configure_connection, initialize_schema


class CreateUsersTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "store.db"
        conn = self.connect()
        try:
            initialize_schema(conn)
            conn.execute(
                """
                INSERT INTO users
                    (user_id, username, password_hash, role, quota_bytes, created)
                VALUES ('durable-id', 'alice', ?, 'user', 1073741824, 'before')
                """,
                (bcrypt.hashpw(b"OldPassw0rd!", bcrypt.gensalt()),),
            )
            conn.execute(
                """
                INSERT INTO refresh_tokens
                    (user_id, token, expires, created, allowed_ip)
                VALUES ('durable-id', ?, ?, ?, '127.0.0.1')
                """,
                (
                    hashlib.sha256(b"old-refresh").hexdigest(),
                    int(time.time()) + 3600,
                    int(time.time()),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def connect(self):
        return configure_connection(sqlite3.connect(self.db_path))

    def test_case_mismatched_replace_updates_exact_user_and_revokes_tokens(self):
        stdout = io.StringIO()
        with (
            mock.patch(
                "builtins.input",
                side_effect=["Alice", "user", "Alice", "Anderson", "5", "GB", "y"],
            ),
            mock.patch(
                "getpass.getpass",
                side_effect=["NewPassw0rd!", "NewPassw0rd!"],
            ),
            contextlib.redirect_stdout(stdout),
        ):
            result = create_users.main(str(self.db_path))

        self.assertEqual(result, 0)
        self.assertIn("replaced successfully", stdout.getvalue())
        conn = self.connect()
        try:
            row = conn.execute(
                """
                SELECT username, password_hash, quota_bytes, first_name, last_name
                FROM users WHERE user_id = 'durable-id'
                """
            ).fetchone()
            token_count = conn.execute(
                """
                SELECT COUNT(*) FROM refresh_tokens
                WHERE user_id = 'durable-id'
                """
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(row[0], "alice")
        self.assertTrue(bcrypt.checkpw(b"NewPassw0rd!", row[1]))
        self.assertEqual(row[2], 5 * 1024 ** 3)
        self.assertEqual((row[3], row[4]), ("Alice", "Anderson"))
        self.assertEqual(token_count, 0)

    def _create_with_quota(self, amount, unit):
        with (
            mock.patch(
                "builtins.input",
                side_effect=["bob", "user", "Bob", "Builder", amount, unit],
            ),
            mock.patch(
                "getpass.getpass",
                side_effect=["NewPassw0rd!", "NewPassw0rd!"],
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = create_users.main(str(self.db_path))
        self.assertEqual(result, 0)
        conn = self.connect()
        try:
            return conn.execute(
                "SELECT quota_bytes FROM users WHERE username = 'bob'"
            ).fetchone()[0]
        finally:
            conn.close()

    def test_quota_unit_megabytes_stored_as_bytes(self):
        self.assertEqual(self._create_with_quota("500", "MB"), 500 * 1024 ** 2)

    def test_quota_unit_terabytes_stored_as_bytes(self):
        self.assertEqual(self._create_with_quota("2", "TB"), 2 * 1024 ** 4)

    def test_quota_unit_defaults_to_gb_when_blank(self):
        self.assertEqual(self._create_with_quota("3", ""), 3 * 1024 ** 3)
