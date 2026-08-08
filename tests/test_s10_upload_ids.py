import importlib
import io
import os
import re
import sys
import tempfile
import unittest

from pathlib import Path

import jwt


class ServerIssuedUploadIdTest(unittest.TestCase):
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

    def upload(self, name, content):
        return self.client.post(
            "/upload",
            headers=self.headers,
            data={
                "file": (io.BytesIO(content), name),
                "fileName": name,
                "fileUUID": "attacker-reused-id",
                "chunkIndex": "0",
                "chunkOffset": "0",
                "totalChunks": "1",
                "fileSize": str(len(content)),
                "chunkSize": str(len(content)),

                "expires": "3600",
                "overwrite": "0",
            },
            content_type="multipart/form-data",
        )

    def test_reused_client_uuid_cannot_reuse_a_finished_object(self):
        first = self.upload("first.bin", b"first")
        second = self.upload("second.bin", b"second")
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        self.assertEqual(second.status_code, 200, second.get_data(as_text=True))

        with self.server.db_session() as conn:
            rows = conn.execute(
                "SELECT file_id, file_name FROM links ORDER BY file_name"
            ).fetchall()

        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0][0], rows[1][0])
        for file_id, file_name in rows:
            self.assertRegex(file_id, re.compile(r"^user-id/[0-9a-f-]{36}$"))
            expected = b"first" if file_name == "first.bin" else b"second"
            self.assertEqual(
                self.server.resolve_upload_path(file_id).read_bytes(),
                expected,
            )
