import bcrypt
import importlib
import os
import sys
import tempfile
import unittest

from pathlib import Path

import jwt


class PasswordWriteOrderTest(unittest.TestCase):
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
        password_hash = bcrypt.hashpw(self.old_password.encode(), bcrypt.gensalt())
        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO users
                    (user_id, username, password_hash, role, quota_bytes, created)
                VALUES ('user-id', 'user', ?, 'user', 1073741824, 'now')
                """,
                (password_hash,),
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

    def stored_hash(self):
        with self.server.db_session() as conn:
            return conn.execute(
                "SELECT password_hash FROM users WHERE user_id = 'user-id'"
            ).fetchone()[0]

    def test_rejected_password_change_never_writes(self):
        for new_password, repeat_password in (
            ("weak", "different"),
            ("weak", "weak"),
        ):
            with self.subTest(new_password=new_password):
                response = self.client.post(
                    "/changepwd",
                    headers=self.headers,
                    json={
                        "old_password": self.old_password,
                        "new_password": new_password,
                        "repeat_password": repeat_password,
                    },
                )
                self.assertEqual(response.status_code, 400)
                self.assertTrue(
                    bcrypt.checkpw(self.old_password.encode(), self.stored_hash())
                )
                self.assertFalse(bcrypt.checkpw(b"weak", self.stored_hash()))
