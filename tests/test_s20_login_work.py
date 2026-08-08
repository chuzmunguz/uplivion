import bcrypt
import importlib
import os
import sys
import tempfile
import time
import unittest

from unittest import mock


class LoginWorkTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = self.temp_dir.name
        os.environ["UPLIVION_UPLOAD_DIR"] = os.path.join(root, "share")
        os.environ["UPLIVION_DB_PATH"] = os.path.join(root, "store.db")
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
                VALUES ('user-id', 'known', ?, 'user', 1073741824, 'now')
                """,
                (bcrypt.hashpw(b"KnownPassw0rd!", bcrypt.gensalt()),),
            )
            conn.commit()

    def tearDown(self):
        sys.modules.pop("server", None)
        self.temp_dir.cleanup()

    def attempt_with_spy(self, username):
        with mock.patch.object(
            self.server.bcrypt, "checkpw", return_value=False
        ) as checkpw:
            response = self.client.post(
                "/login",
                json={"username": username, "password": "WrongPassw0rd!"},
            )
        return response, checkpw.call_args_list

    def test_known_and_unknown_user_each_do_one_bcrypt_check(self):
        known_response, known_calls = self.attempt_with_spy("known")
        unknown_response, unknown_calls = self.attempt_with_spy("unknown")

        self.assertEqual(known_response.status_code, 401)
        self.assertEqual(unknown_response.status_code, 401)
        self.assertEqual(known_response.get_json(), unknown_response.get_json())
        self.assertEqual(len(known_calls), 1)
        self.assertEqual(len(unknown_calls), 1)
        self.assertEqual(
            unknown_calls[0].args[1], self.server.DUMMY_PASSWORD_HASH
        )

    def test_real_route_timing_has_no_immediate_unknown_user_return(self):
        elapsed = {}
        for username in ("known", "unknown"):
            started = time.perf_counter()
            response = self.client.post(
                "/login",
                json={"username": username, "password": "WrongPassw0rd!"},
            )
            elapsed[username] = time.perf_counter() - started
            self.assertEqual(response.status_code, 401)

        self.assertGreater(min(elapsed.values()), 0.05)
        self.assertLess(
            max(elapsed.values()) / min(elapsed.values()),
            3.0,
            elapsed,
        )
