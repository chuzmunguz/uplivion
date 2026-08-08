import importlib
import io
import os
import sys
import tempfile
import unittest

from pathlib import Path

import jwt


class OverwriteFlowTest(unittest.TestCase):
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
                    (user_id, username, password_hash, quota_bytes, created)
                VALUES ('user-id', 'user', X'00', 1073741824, 'now')
                """
            )
        token = jwt.encode(
            {"user_id": "user-id", "username": "user", "ip": "127.0.0.1", "aver": 0},
            os.environ["ACCESS_TOKEN_SECRET"],
            algorithm="HS256",
        )
        self.headers = {"Authorization": f"Bearer {token}"}

    def tearDown(self):
        sys.modules.pop("server", None)
        self.temp_dir.cleanup()

    def upload(self, content, overwrite):
        return self.client.post(
            "/upload",
            headers=self.headers,
            data={
                "file": (io.BytesIO(content), "same.bin"),
                "fileName": "same.bin",
                "chunkIndex": "0",
                "chunkOffset": "0",
                "totalChunks": "1",
                "fileSize": str(len(content)),
                "chunkSize": str(len(content)),

                "expires": "3600",
                "overwrite": str(overwrite),
            },
            content_type="multipart/form-data",
        )

    def test_conflict_then_clean_overwrite_replaces_the_owned_object(self):
        first = self.upload(b"old", 0)
        self.assertEqual(first.status_code, 200)
        with self.server.db_session() as conn:
            old_id = conn.execute(
                "SELECT file_id FROM links WHERE file_name = 'same.bin'"
            ).fetchone()[0]

        conflict = self.upload(b"new", 0)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["fileID"], old_id)

        replacement = self.upload(b"new", 1)
        self.assertEqual(
            replacement.status_code,
            200,
            replacement.get_data(as_text=True),
        )
        with self.server.db_session() as conn:
            rows = conn.execute(
                "SELECT file_id FROM links WHERE file_name = 'same.bin'"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0][0], old_id)
        self.assertEqual(
            self.server.resolve_upload_path(rows[0][0]).read_bytes(),
            b"new",
        )
        self.assertFalse(self.server.resolve_upload_path(old_id).exists())
