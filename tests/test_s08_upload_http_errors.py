import importlib
import os
import sys
import tempfile
import unittest

from pathlib import Path


class UploadHttpErrorTest(unittest.TestCase):
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

    def tearDown(self):
        sys.modules.pop("server", None)
        self.temp_dir.cleanup()

    def test_invalid_token_remains_401(self):
        response = self.client.post(
            "/upload",
            headers={"Authorization": "Bearer garbage"},
        )
        self.assertEqual(response.status_code, 401)

    def test_denied_source_remains_403(self):
        response = self.client.post(
            "/upload",
            headers={"X-Forwarded-For": "203.0.113.9"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(response.status_code, 403)
