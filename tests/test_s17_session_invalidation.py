import bcrypt
import hashlib
import importlib
import os
import sys
import tempfile
import time
import unittest

from pathlib import Path

import jwt


ROOT = Path(__file__).resolve().parents[1]


class SessionInvalidationTest(unittest.TestCase):
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
        self.old_password = "OldPassw0rd!"
        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO users
                    (user_id, username, password_hash, role, quota_bytes, created)
                VALUES ('user-id', 'user', ?, 'user', 1073741824, 'now')
                """,
                (bcrypt.hashpw(self.old_password.encode(), bcrypt.gensalt()),),
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

    def add_refresh(self, raw_token, allowed_ip="127.0.0.1"):
        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO refresh_tokens
                    (user_id, token, expires, created, allowed_ip)
                VALUES ('user-id', ?, ?, ?, ?)
                """,
                (
                    hashlib.sha256(raw_token.encode()).hexdigest(),
                    int(time.time()) + 3600,
                    int(time.time()),
                    allowed_ip,
                ),
            )
            conn.commit()

    def test_password_change_revokes_refresh_and_invalidates_access_tokens(self):
        self.add_refresh("stolen-refresh")
        response = self.client.post(
            "/changepwd",
            headers=self.headers,
            json={
                "old_password": self.old_password,
                "new_password": "NewPassw0rd!",
                "repeat_password": "NewPassw0rd!",
            },
        )
        self.assertEqual(response.status_code, 200)
        with self.server.db_session() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM refresh_tokens WHERE user_id = 'user-id'"
            ).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(
            self.client.post("/check", headers=self.headers).status_code, 401
        )

    def test_refresh_token_is_bound_to_issuing_address(self):
        self.add_refresh("bound-refresh", allowed_ip="192.168.1.50")
        self.client.set_cookie(
            "refresh_token", "bound-refresh", path="/session"
        )
        response = self.client.post(
            "/session", environ_base={"REMOTE_ADDR": "127.0.0.1"}
        )
        self.assertEqual(response.status_code, 401)

    def test_cli_replacement_revokes_by_durable_user_id(self):
        cli = (ROOT / "create_users.py").read_text(encoding="utf-8")
        self.assertIn(
            '"DELETE FROM refresh_tokens WHERE user_id = ?", (user_id,)',
            cli,
        )
