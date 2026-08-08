import importlib
import os
import sys
import tempfile
import unittest

from pathlib import Path


class ClientIpValidationTest(unittest.TestCase):
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

    def test_malformed_forwarded_address_fails_closed_on_gated_routes(self):
        for route in ("/check", "/upload"):
            with self.subTest(route=route):
                response = self.client.post(
                    route,
                    headers={"X-Forwarded-For": "not-an-ip"},
                    environ_base={"REMOTE_ADDR": "127.0.0.1"},
                )
                self.assertEqual(response.status_code, 403)
