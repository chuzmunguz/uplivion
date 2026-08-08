import importlib
import io
import os
import sys
import tempfile
import unittest

from pathlib import Path

import jwt


class UploadManifestTest(unittest.TestCase):
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

    def chunk(self, content, **overrides):
        fields = {
            "file": (io.BytesIO(content), "file.bin"),
            "fileName": "file.bin",
            "chunkIndex": "0",
            "chunkOffset": "0",
            "totalChunks": "1",
            "fileSize": str(len(content)),
            "chunkSize": str(len(content)),

            "expires": "3600",
            "overwrite": "0",
        }
        fields.update(overrides)
        return self.client.post(
            "/upload",
            headers=self.headers,
            data=fields,
            content_type="multipart/form-data",
        )

    def test_negative_size_and_sparse_offset_fail_without_state(self):
        negative = self.chunk(b"0123456789", fileSize="-1", chunkSize="10")
        sparse = self.chunk(
            b"x",
            fileName="sparse.bin",
            chunkOffset=str(5 * 1024 * 1024 * 1024),
        )
        self.assertEqual(negative.status_code, 400)
        self.assertEqual(sparse.status_code, 400)
        with self.server.db_session() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM links").fetchone()[0], 0)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM quota_reservations").fetchone()[0],
                0,
            )

    def test_later_chunk_uses_immutable_canonical_manifest(self):
        first = self.chunk(
            b"a",
            totalChunks="2",
            fileSize="2",
            chunkSize="1",
        )
        self.assertEqual(first.status_code, 200)
        upload_id = first.get_json()["fileID"]

        second = self.chunk(
            b"b",
            uploadID=upload_id,
            chunkIndex="1",
            chunkOffset="1",
            totalChunks="999",
            fileSize="999",
            chunkSize="999",
            expires="999999999",
        )
        self.assertEqual(second.status_code, 200, second.get_data(as_text=True))
        with self.server.db_session() as conn:
            row = conn.execute(
                "SELECT size, expires FROM links WHERE user_id = 'user-id'"
            ).fetchone()
        self.assertEqual(row[0], 2)
        self.assertLess(row[1], int(self.server.time.time()) + 4000)
