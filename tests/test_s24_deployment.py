import subprocess
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DeploymentContractTest(unittest.TestCase):
    def test_deploy_paths_match_tracked_repository_files(self):
        # install.sh copies a named list rather than deriving it from git, so
        # it stays free of a git dependency on the deploy target — but that
        # means a hand-maintained list can drift from the repository without
        # anyone noticing. This check runs git (available here, even though
        # install.sh deliberately avoids it) and catches that drift instead.
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        deploy_block = installer.split("DEPLOY_PATHS=(", 1)[1].split("\n)", 1)[0]
        deploy_paths = {
            line.strip() for line in deploy_block.splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        tracked_top_level = {
            entry.split("/", 1)[0]
            for entry in subprocess.run(
                ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
            ).stdout.split()
        }
        # .gitignore is repository metadata, not part of the deployed application.
        tracked_top_level.discard(".gitignore")
        # LICENSE and README.md are informational; the checkout is their canonical
        # home and nothing at runtime reads them from $APP.
        tracked_top_level.discard("LICENSE")
        tracked_top_level.discard("README.md")
        # logos/ holds source art the deployed public/ icons are generated from;
        # intentionally not deployed.
        tracked_top_level.discard("logos")
        # Operator tooling — create_user.sh and seed.sh both run against the
        # installed app's venv/DB from the checkout by design (see their own
        # header comments); uninstall.sh likewise only ever needs the checkout,
        # since removing $APP is the point. None are meant to exist under $APP.
        tracked_top_level.discard("create_user.sh")
        tracked_top_level.discard("seed.sh")
        tracked_top_level.discard("uninstall.sh")
        # uplivion.nginx and fail2ban/ are only ever read from $SOURCE at
        # install time (vhost rendering, jail install); tests/ is the dev test
        # rig. Nothing at runtime reads any of them from $APP.
        tracked_top_level.discard("uplivion.nginx")
        tracked_top_level.discard("fail2ban")
        tracked_top_level.discard("tests")
        self.assertNotIn("rm -rf \"$APP\"/*", installer)
        self.assertEqual(
            deploy_paths, tracked_top_level,
            f"missing {sorted(tracked_top_level - deploy_paths)}, "
            f"extra {sorted(deploy_paths - tracked_top_level)}",
        )

    def test_code_state_and_public_paths_have_matching_consumers(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        cli = (ROOT / "create_users.py").read_text(encoding="utf-8")
        environment = (ROOT / "uplivion.env.example").read_text(encoding="utf-8")
        self.assertIn('"/var/lib/uplivion/share"', server)
        self.assertIn('"/var/lib/uplivion/store.db"', server)
        self.assertIn('"/var/lib/uplivion/store.db"', cli)
        self.assertIn("UPLIVION_UPLOAD_DIR=/var/lib/uplivion/share", environment)
        self.assertIn("UPLIVION_DB_PATH=/var/lib/uplivion/store.db", environment)

        config = (ROOT / "uplivion.nginx").read_text(encoding="utf-8")
        self.assertIn("root /var/www/uplivion/public/;", config)
        self.assertIn("alias /var/lib/uplivion/share/;", config)
        self.assertNotIn("/home/uplivion/uplivion_webapp", config)

    def test_admin_endpoints_are_proxied_to_backend(self):
        # The backend serves /admin/* (user management). Without a matching
        # nginx location these requests fall through to the static `location /`
        # and 404 (GET) or 403 (POST/DELETE), silently breaking the admin panel.
        config = (ROOT / "uplivion.nginx").read_text(encoding="utf-8")
        self.assertIn("location ~ ^/admin(/|$) {", config)
        admin_block = config.split("location ~ ^/admin(/|$) {", 1)[1].split("}", 1)[0]
        self.assertIn("proxy_pass http://127.0.0.1:8000$request_uri;", admin_block)
        # Deleting a user is a DELETE; the method allowlist must permit it.
        self.assertIn("limit_except GET POST DELETE", admin_block)

    def test_profile_endpoints_are_proxied_to_backend(self):
        # Self-service /profile (name via POST, own files/account via DELETE)
        # needs its own location, or requests fall through to the static root.
        config = (ROOT / "uplivion.nginx").read_text(encoding="utf-8")
        self.assertIn("location ~ ^/profile(/|$) {", config)
        block = config.split("location ~ ^/profile(/|$) {", 1)[1].split("}", 1)[0]
        self.assertIn("proxy_pass http://127.0.0.1:8000$request_uri;", block)
        self.assertIn("limit_except POST DELETE", block)

    def test_service_has_one_worker_and_least_privilege_boundaries(self):
        service = (ROOT / "uplivion.service").read_text(encoding="utf-8")
        for contract in (
            "User=uplivion",
            "WorkingDirectory=/var/www/uplivion",
            "EnvironmentFile=/etc/uplivion/uplivion.env",
            "--workers 1 --worker-class sync --timeout 300",
            "Restart=on-failure",
            "UMask=0027",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=strict",
            "ProtectHome=true",
            "ReadWritePaths=/var/lib/uplivion",
            "PYTHONDONTWRITEBYTECODE=1",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, service)
        self.assertNotIn("searxng", service)

    def test_install_is_explicit(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("--keep-data|--wipe-data", installer)
        self.assertIn('chown -R root:root "$APP"', installer)
        self.assertIn('install -d -m 2750 -o uplivion -g www-data "$SHARE"', installer)
        self.assertIn("openssl rand -hex 32", installer)
