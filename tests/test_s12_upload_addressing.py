import importlib
import io
import os
import sys
import tempfile
import unittest

from pathlib import Path

import jwt


class UploadAddressingTest(unittest.TestCase):
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

    def first_chunk(self, name):
        return self.client.post(
            "/upload",
            headers=self.headers,
            data={
                "file": (io.BytesIO(b"a"), name),
                "fileName": name,
                "chunkIndex": "0",
                "chunkOffset": "0",
                "totalChunks": "2",
                "fileSize": "2",
                "chunkSize": "1",

                "expires": "3600",
                "overwrite": "0",
            },
            content_type="multipart/form-data",
        )

    def test_progress_and_cancel_use_server_upload_id(self):
        response = self.first_chunk("space name.bin")
        self.assertEqual(response.status_code, 200)
        upload_id = response.get_json()["fileID"]

        progress = self.client.get(
            "/progress",
            headers={**self.headers, "X-Upload-ID": upload_id},
        )
        self.assertEqual(progress.status_code, 200)
        self.assertEqual(progress.get_json()["uploadedChunks"], [0])

        cancel = self.client.post(
            "/cancel",
            headers={**self.headers, "Content-Type": "application/json"},
            json={"uploadID": upload_id},
        )
        self.assertEqual(cancel.status_code, 200)
        self.assertFalse(self.server.resolve_upload_path(upload_id).exists())
        with self.server.db_session() as conn:
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM upload_metadata WHERE file_id = ?",
                    (upload_id,),
                ).fetchone()
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM quota_reservations WHERE file_id = ?",
                    (upload_id,),
                ).fetchone()
            )

    def test_colliding_sanitized_names_have_distinct_state(self):
        first = self.first_chunk("a b.bin").get_json()["fileID"]
        second = self.first_chunk("a+b.bin").get_json()["fileID"]
        self.assertNotEqual(first, second)
        self.assertTrue(self.server.resolve_upload_path(first).exists())
        self.assertTrue(self.server.resolve_upload_path(second).exists())
