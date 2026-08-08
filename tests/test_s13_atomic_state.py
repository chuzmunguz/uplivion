import importlib
import os
import sqlite3
import sys
import tempfile
import threading
import unittest

from pathlib import Path

from store_schema import configure_connection, initialize_schema


class AtomicStateTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        os.environ["UPLIVION_UPLOAD_DIR"] = str(root / "share")
        os.environ["UPLIVION_DB_PATH"] = str(root / "app.db")
        os.environ["SECRET_KEY"] = "s" * 32
        os.environ["ACCESS_TOKEN_SECRET"] = "a" * 32
        sys.modules.pop("server", None)
        self.server = importlib.import_module("server")

    def tearDown(self):
        sys.modules.pop("server", None)
        self.temp_dir.cleanup()

    def test_schema_rejects_duplicate_per_user_filename(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            conn = configure_connection(
                sqlite3.connect(Path(temp_dir) / "store.db")
            )
            initialize_schema(conn)
            conn.execute(
                """
                INSERT INTO users
                    (user_id, username, password_hash, role, quota_bytes, created)
                VALUES ('user-id', 'user', X'00', 'user', 1073741824, 'now')
                """
            )
            conn.execute(
                """
                INSERT INTO links (user_id, file_id, file_name, size)
                VALUES ('user-id', 'one', 'same.bin', 1)
                """
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO links (user_id, file_id, file_name, size)
                    VALUES ('user-id', 'two', 'same.bin', 1)
                    """
                )
            conn.close()

    def test_in_memory_limiter_is_thread_atomic(self):
        bucket = {}
        statuses = []
        barrier = threading.Barrier(40)

        def consume():
            barrier.wait()
            try:
                self.server.rate_limit("127.0.0.1", 1000, bucket, 1000)
                statuses.append(200)
            except Exception as exc:
                statuses.append(getattr(exc, "code", 500))

        with self.server.app.test_request_context("/"):
            threads = [threading.Thread(target=consume) for _ in range(40)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(statuses.count(200), 15)
        self.assertEqual(statuses.count(429), 25)
