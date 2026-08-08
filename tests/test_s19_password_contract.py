import bcrypt
import importlib
import os
import sys
import tempfile
import unittest

from pathlib import Path

import jwt

from request_validation import PASSWORD_MAX_BYTES, validate_password_policy


ROOT = Path(__file__).resolve().parents[1]


class PasswordContractTest(unittest.TestCase):
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
                VALUES ('user-id', 'user', ?, 'user', 1073741824, 'now')
                """,
                (bcrypt.hashpw(b"ValidPassw0rd!", bcrypt.gensalt()),),
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

    def test_login_rejects_overlong_ascii_and_unicode_without_bcrypt_error(self):
        for password in ("x" * 73, "é" * 37):
            with self.subTest(byte_length=len(password.encode("utf-8"))):
                response = self.client.post(
                    "/login", json={"username": "user", "password": password}
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(
                    response.get_json(),
                    {"error": "Invalid username or password"},
                )

    def test_change_password_rejects_overlong_old_password_cleanly(self):
        response = self.client.post(
            "/changepwd",
            headers=self.headers,
            json={
                "old_password": "x" * 73,
                "new_password": "NewPassw0rd!",
                "repeat_password": "NewPassw0rd!",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(), {"error": "Old password is incorrect"}
        )

    def test_shared_policy_and_browser_mirror_use_72_utf8_bytes(self):
        self.assertEqual(PASSWORD_MAX_BYTES, 72)
        self.assertTrue(validate_password_policy("ValidPassw0rd!")[0])
        self.assertFalse(validate_password_policy("é" * 37)[0])

        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "public" / "uplivion.js").read_text(encoding="utf-8")
        self.assertEqual(html.count('maxlength="72"'), 4)
        self.assertEqual(html.count('maxlength="25"'), 1)
        self.assertIn("new TextEncoder().encode(value).length", javascript)
        self.assertIn("const PASSWORD_MAX_BYTES = 72;", javascript)
        self.assertNotIn("cannot exceed 24 characters", javascript)
