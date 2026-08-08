import importlib
import os
import sys
import tempfile
import time
import unittest

from pathlib import Path

import jwt


class LogoutRevocationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        os.environ["UPLIVION_UPLOAD_DIR"] = str(root / "share")
        os.environ["UPLIVION_DB_PATH"] = str(root / "store.db")
        os.environ["SECRET_KEY"] = "s" * 32
        os.environ["ACCESS_TOKEN_SECRET"] = "a" * 32
        sys.modules.pop("server", None)
        self.server = importlib.import_module("server")
        self.client = self.server.app.test_client()
        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO users
                    (user_id, username, password_hash, role, quota_bytes, created)
                VALUES ('user-id', 'user', X'00', 'user', 1073741824, 'now')
                """
            )
            conn.executemany(
                """
                INSERT INTO refresh_tokens
                    (user_id, token, expires, created, allowed_ip)
                VALUES ('user-id', ?, ?, ?, '127.0.0.1')
                """,
                (
                    ("token-one", int(time.time()) + 3600, int(time.time())),
                    ("token-two", int(time.time()) + 3600, int(time.time())),
                ),
            )
            conn.commit()
        token = jwt.encode(
            {"user_id": "user-id", "username": "user", "ip": "127.0.0.1", "aver": 0},
            os.environ["ACCESS_TOKEN_SECRET"],
            algorithm="HS256",
        )
        self.headers = {"Authorization": f"Bearer {token}"}

    def tearDown(self):
        sys.modules.pop("server", None)
        self.temp_dir.cleanup()

    def test_logout_revokes_all_user_sessions_without_cookie(self):
        response = self.client.post("/logout", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        with self.server.db_session() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM refresh_tokens WHERE user_id = 'user-id'"
            ).fetchone()[0]
        self.assertEqual(count, 0)
