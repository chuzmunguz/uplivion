import importlib
import io
import os
import sys
import tempfile
import unittest

from pathlib import Path

import jwt


class StorageSecurityTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.upload_dir = self.root / "share"
        self.db_path = self.root / "store.db"
        os.environ["UPLIVION_UPLOAD_DIR"] = str(self.upload_dir)
        os.environ["UPLIVION_DB_PATH"] = str(self.db_path)
        os.environ["SECRET_KEY"] = "test-hmac-secret"
        os.environ["ACCESS_TOKEN_SECRET"] = "test-access-secret" * 2
        sys.modules.pop("server", None)
        self.server = importlib.import_module("server")
        self.client = self.server.app.test_client()

        with self.server.db_session() as conn:
            conn.executemany(
                """
                INSERT INTO users
                    (user_id, username, password_hash, role, quota_bytes, created)
                VALUES (?, ?, ?, 'user', 1073741824, '2026-07-26 00:00:00')
                """,
                (
                    ("alice-id", "alice", b"unused"),
                    ("bob-id", "bob", b"unused"),
                ),
            )
            conn.commit()

    def tearDown(self):
        sys.modules.pop("server", None)
        self.temp_dir.cleanup()

    def access_header(self, user_id, username):
        token = jwt.encode(
            {"user_id": user_id, "username": username, "ip": "127.0.0.1", "aver": 0},
            os.environ["ACCESS_TOKEN_SECRET"],
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {token}"}

    def test_resolver_rejects_absolute_traversal_and_symlink_escape(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.upload_dir / "escape").symlink_to(outside, target_is_directory=True)

        for file_id in (str(outside / "victim"), "../victim", "escape/victim"):
            with self.subTest(file_id=file_id):
                with self.assertRaises(ValueError):
                    self.server.resolve_upload_path(file_id)

    def test_overwrite_ignores_foreign_and_absolute_client_file_ids(self):
        victim_id = "alice_existing"
        victim_path = self.server.resolve_upload_path(victim_id)
        victim_path.write_bytes(b"alice data")
        outside_path = self.root / "outside-victim"
        outside_path.write_bytes(b"outside data")

        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO links
                    (user_id, file_id, file_name, size, hash, uploaded,
                     linktoken, expires, created, revoked)
                VALUES (?, ?, ?, ?, '', '', '', ?, '', 0)
                """,
                ("alice-id", victim_id, "alice.bin", 10, 2_000_000_000),
            )
            conn.commit()

        for index, supplied_id in enumerate((victim_id, str(outside_path), "../outside-victim")):
            with self.subTest(supplied_id=supplied_id):
                response = self.client.post(
                    "/upload",
                    headers=self.access_header("bob-id", "bob"),
                    data={
                        "file": (io.BytesIO(b"bob data"), "bob.bin"),
                        "fileName": "bob.bin",
                        "chunkIndex": "0",
                        "chunkOffset": "0",
                        "totalChunks": "1",
                        "fileSize": "8",
                        "chunkSize": "8",

                        "expires": "3600",
                        "overwrite": "1",
                    },
                    content_type="multipart/form-data",
                )
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
                self.assertEqual(victim_path.read_bytes(), b"alice data")
                self.assertEqual(outside_path.read_bytes(), b"outside data")
                with self.server.db_session() as conn:
                    row = conn.execute(
                        "SELECT file_id FROM links WHERE user_id = ? AND file_name = ?",
                        ("alice-id", "alice.bin"),
                    ).fetchone()
                self.assertEqual(row, (victim_id,))
