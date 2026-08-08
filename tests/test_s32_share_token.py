import base64
import hashlib
import hmac
import importlib
import os
import sys
import tempfile
import time
import unittest

from pathlib import Path


class ShareTokenTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.upload_dir = root / "share"
        os.environ["UPLIVION_UPLOAD_DIR"] = str(self.upload_dir)
        os.environ["UPLIVION_DB_PATH"] = str(root / "store.db")
        os.environ["SECRET_KEY"] = "s" * 32
        os.environ["ACCESS_TOKEN_SECRET"] = "a" * 32
        sys.modules.pop("server", None)
        self.server = importlib.import_module("server")
        self.client = self.server.app.test_client()

    def tearDown(self):
        sys.modules.pop("server", None)
        self.temp_dir.cleanup()

    def add_link(self, token=None):
        file_id = "user-id_file"
        expires = int(time.time()) + 3600
        if token is None:
            token = self.expected_token(file_id, expires)
        (self.upload_dir / file_id).write_bytes(b"content")
        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO users
                    (user_id, username, password_hash, quota_bytes, created)
                VALUES ('user-id', 'user', X'00', 1073741824, 'now')
                """
            )
            conn.execute(
                """
                INSERT INTO links
                    (user_id, file_id, file_name, size, hash, uploaded,
                     linktoken, expires, created, revoked)
                VALUES ('user-id', ?, 'file.txt', 7, '', 'now',
                        ?, ?, 'now', 0)
                """,
                (file_id, token, expires),
            )
            conn.commit()
        return file_id, expires, token

    def expected_token(self, file_id, expires):
        digest = hmac.new(
            os.environ["SECRET_KEY"].encode(),
            f"{file_id}:{expires}".encode(),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def test_valid_token_is_accepted(self):
        file_id, _, token = self.add_link()
        response = self.client.get(f"/share/{token}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["X-Accel-Redirect"],
            f"/internal_share/{file_id}",
        )

    def test_database_token_that_fails_constant_time_recompute_is_rejected(self):
        token = "tampered-token"
        self.add_link(token)
        response = self.client.get(f"/share/{token}")
        self.assertEqual(response.status_code, 404)
