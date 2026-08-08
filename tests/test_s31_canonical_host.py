import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CanonicalHostTest(unittest.TestCase):
    def test_proxy_headers_set_at_server_level(self):
        config = (ROOT / "uplivion.nginx").read_text(encoding="utf-8")
        self.assertIn("proxy_set_header Host $host;", config)
        self.assertIn("proxy_set_header X-Real-IP $remote_addr;", config)
        self.assertIn("proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;", config)
        self.assertIn("proxy_set_header X-Forwarded-Proto $scheme;", config)

