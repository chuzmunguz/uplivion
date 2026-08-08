import importlib
import os
import sqlite3
import sys
import tempfile
import unittest

from pathlib import Path
from unittest import mock

import jwt

from username_validation import validate_username


ROOT = Path(__file__).resolve().parents[1]


class RequestErrorContractTest(unittest.TestCase):
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

    def tearDown(self):
        sys.modules.pop("server", None)
        self.temp_dir.cleanup()

    def access_headers(self):
        token = jwt.encode(
            {"user_id": "user-id", "username": "user", "ip": "127.0.0.1", "aver": 0},
            os.environ["ACCESS_TOKEN_SECRET"],
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    def test_login_rejects_non_object_and_log_forging_username(self):
        self.assertEqual(self.client.post("/login", json=None).status_code, 400)
        response = self.client.post(
            "/login",
            json={"username": "user\nforged", "password": "anything"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.get_json(), {"error": "Invalid username or password"}
        )

    def test_internal_exception_details_never_reach_client(self):
        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO users
                    (user_id, username, password_hash, role, quota_bytes, created)
                VALUES ('user-id', 'user', X'00', 'user', 1073741824, 'now')
                """
            )
            conn.commit()

        private_detail = "/private/share/secret.part"

        real_db_session = self.server.db_session
        call_count = [0]

        @self.server.contextmanager
        def failing_db_session():
            call_count[0] += 1
            if call_count[0] > 1:
                raise sqlite3.OperationalError(private_detail)
            with real_db_session() as conn:
                yield conn

        with mock.patch.object(self.server, "db_session", failing_db_session):
            response = self.client.post(
                "/links/user-id/safe/revoke",
                headers=self.access_headers(),
            )

        self.assertEqual(response.status_code, 500)
        body = response.get_json()
        self.assertEqual(body["error"], "Internal server error")
        self.assertEqual(len(body["request_id"]), 16)
        self.assertNotIn("details", body)
        self.assertNotIn(private_detail, response.get_data(as_text=True))

    def test_shared_username_policy_bounds_operator_and_api_input(self):
        for value in ("", "x" * 33, "line\nbreak", ["not", "text"]):
            with self.subTest(value=value):
                valid, _, _ = validate_username(value)
                self.assertFalse(valid)

        cli = (ROOT / "create_users.py").read_text(encoding="utf-8")
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn("validate_username(", cli)
        self.assertIn('input("Enter username: ")', cli)
        self.assertIn("validate_username(data.get(\"username\"))", server)
