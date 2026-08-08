import hashlib
import importlib
import os
import sys
import tempfile
import threading
import time
import unittest

from pathlib import Path


class RefreshRotationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        os.environ["UPLIVION_UPLOAD_DIR"] = str(root / "share")
        os.environ["UPLIVION_DB_PATH"] = str(root / "store.db")
        os.environ["SECRET_KEY"] = "s" * 32
        os.environ["ACCESS_TOKEN_SECRET"] = "a" * 32
        sys.modules.pop("server", None)
        self.server = importlib.import_module("server")
        self.raw_token = "single-use-token"
        token_hash = hashlib.sha256(self.raw_token.encode()).hexdigest()
        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO users
                    (user_id, username, password_hash, role, quota_bytes, created)
                VALUES ('user-id', 'user', X'00', 'user', 1073741824, 'now')
                """
            )
            conn.execute(
                """
                INSERT INTO refresh_tokens
                    (user_id, token, expires, created, allowed_ip)
                VALUES ('user-id', ?, ?, ?, '127.0.0.1')
                """,
                (token_hash, int(time.time()) + 3600, int(time.time())),
            )
            conn.commit()

    def tearDown(self):
        sys.modules.pop("server", None)
        self.temp_dir.cleanup()

    def test_concurrent_replay_has_one_winner_and_one_descendant(self):
        barrier = threading.Barrier(2)
        statuses = []

        def refresh():
            client = self.server.app.test_client()
            client.set_cookie("refresh_token", self.raw_token, path="/session")
            barrier.wait()
            statuses.append(client.post("/session").status_code)

        threads = [threading.Thread(target=refresh) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(statuses), [200, 401])
        with self.server.db_session() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM refresh_tokens WHERE user_id = 'user-id'"
            ).fetchone()[0]
        self.assertEqual(count, 1)
