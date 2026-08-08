import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ResourceBoundsTest(unittest.TestCase):
    def test_flask_caps_one_multipart_chunk_with_headroom(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn(
            'app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024',
            server,
        )
        self.assertIn("file.stream.read(MAX_CHUNK_SIZE + 1)", server)
        self.assertNotIn("file.stream.read()", server)

    def test_nginx_uses_small_default_and_upload_only_raise(self):
        config = (ROOT / "uplivion.nginx").read_text(encoding="utf-8")
        self.assertNotIn("client_max_body_size 100g", config)
        self.assertIn("client_max_body_size 1m;", config)
        start = config.index("location /upload {")
        end = config.index("\n    }", start)
        upload = config[start:end]
        self.assertIn("client_max_body_size 6m;", upload)
        self.assertIn("proxy_request_buffering off;", upload)

    def test_worker_model_and_timeout_are_explicit(self):
        service = (ROOT / "uplivion.service").read_text(encoding="utf-8")
        self.assertIn("--workers 1 --worker-class sync --timeout 300", service)

    def test_chunk_completion_uses_count_not_rebuilt_range_sets(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn("uploaded_chunk_count == total_chunks", server)
        self.assertNotIn("set(range(total_chunks))", server)
