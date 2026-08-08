import importlib
import io
import os
import sys
import tempfile
import unittest

from pathlib import Path

import jwt


class ExpiryBoundsTest(unittest.TestCase):
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

    def test_upload_rejects_effectively_permanent_expiry(self):
        response = self.client.post(
            "/upload",
            headers=self.headers,
            data={
                "file": (io.BytesIO(b"x"), "file.bin"),
                "fileName": "file.bin",
                "chunkIndex": "0",
                "chunkOffset": "0",
                "totalChunks": "1",
                "fileSize": "1",
                "chunkSize": "1",

                "expires": str(10**15),
                "overwrite": "0",
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)

    def test_link_renewal_rejects_effectively_permanent_expiry(self):
        # A renewal expiry is bounds-checked before the file lookup, so an
        # out-of-range value is rejected even for a file that does not exist.
        response = self.client.post(
            "/links/user-id/missing/settings",
            headers=self.headers,
            json={"expiry": 10**15},
        )
        self.assertEqual(response.status_code, 400)
