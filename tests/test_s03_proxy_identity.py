import importlib
import os
import sys
import tempfile
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def strip_comments(text):
    """Drop comment lines so prose (which mentions 'location',
    'proxy_set_header') never confuses directive-ordering checks."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


class ProxyIdentityTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        os.environ["UPLIVION_UPLOAD_DIR"] = str(root / "share")
        os.environ["UPLIVION_DB_PATH"] = str(root / "store.db")
        os.environ["SECRET_KEY"] = "test-hmac-secret"
        os.environ["ACCESS_TOKEN_SECRET"] = "test-access-secret" * 2
        sys.modules.pop("server", None)
        self.server = importlib.import_module("server")
        self.client = self.server.app.test_client()

    def tearDown(self):
        sys.modules.pop("server", None)
        self.temp_dir.cleanup()

    def test_attacker_prepended_forwarded_address_is_not_trusted(self):
        response = self.client.post(
            "/check",
            headers={"X-Forwarded-For": "192.168.1.50, 203.0.113.9"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(response.status_code, 403)

    def test_one_proxy_hop_selects_the_forwarded_client(self):
        response = self.client.post(
            "/check",
            headers={"X-Forwarded-For": "192.168.1.50"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(response.status_code, 401)

    def test_forwarded_for_is_overwritten_at_server_scope(self):
        # D-11 retired the per-location include of uplivion-proxy-forwarding.conf.
        # The proxy identity headers are now declared once at server scope in
        # each proxying server block and inherited by every location. Nginx
        # appends the trusted hop via $proxy_add_x_forwarded_for; ProxyFix
        # (x_for=1) then trusts only that rightmost entry, so a client-prepended
        # X-Forwarded-For is neutralized (see the attacker test above).
        config = (ROOT / "uplivion.nginx").read_text(encoding="utf-8")
        # The retired snippet must leave no dangling reference.
        self.assertNotIn("uplivion-proxy-forwarding.conf", config)
        # One overwrite per proxying server block: public share vhost + private.
        overwrite = "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;"
        self.assertEqual(config.count(overwrite), 2)
        proxying = [
            block for block in config.split("server {")[1:] if "proxy_pass" in block
        ]
        self.assertEqual(len(proxying), 2)
        for block in proxying:
            with self.subTest(block=block[:48]):
                # Declared before the first location, so no location overrides
                # (and thereby silently drops) the inherited header set.
                code = strip_comments(block)
                self.assertLess(
                    code.index("proxy_set_header X-Forwarded-For"),
                    code.index("location"),
                )

    def test_private_api_has_nginx_allowlist(self):
        # install.sh generates the allowlist snippet itself from
        # UPLIVION_ALLOWED_IP_RANGES; no real CIDRs are tracked in the repo, so
        # this only checks that the private vhost wires up the include.
        include = "include /etc/nginx/snippets/uplivion-private-allowlist.conf;"
        local_config = (ROOT / "uplivion.nginx").read_text(encoding="utf-8")

        # D-11: included once at server scope in the private vhost, inherited by
        # every private location (previously included per-location).
        self.assertEqual(local_config.count(include), 1)
        private_vhost = local_config.split(
            "# Private HTTPS server for GUI and Backend", 1
        )[1]
        self.assertIn(include, private_vhost)

    def test_public_share_uses_one_proxy_hop_and_local_accel_serving(self):
        local_config = (ROOT / "uplivion.nginx").read_text(encoding="utf-8")
        public_vhost = local_config.split(
            "# Private HTTPS server for GUI and Backend", 1
        )[0]

        self.assertNotIn("proxy_pass http://uplivion.lan/share;", local_config)
        self.assertIn(
            "proxy_pass http://127.0.0.1:8000/share;",
            public_vhost,
        )
        self.assertIn("location /internal_share/ {", public_vhost)
        self.assertIn("internal;", public_vhost)
        self.assertIn("alias /var/lib/uplivion/share/;", public_vhost)
