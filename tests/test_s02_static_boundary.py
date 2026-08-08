import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
TEMPLATES = (
    ROOT / "uplivion.nginx",
)


class StaticBoundaryTest(unittest.TestCase):
    def test_public_tree_contains_only_browser_assets(self):
        actual = {
            path.relative_to(PUBLIC).as_posix()
            for path in PUBLIC.rglob("*")
            if path.is_file()
        }
        self.assertEqual(
            actual,
            {
                "apple-touch-icon.png",
                "error/error.css",
                "error/error.html",
                "favicon.ico",
                "icon-maskable-512.png",
                "icon192.png",
                "icon512.png",
                "index.html",
                "site.webmanifest",
                "style.css",
                "uplivion.js",
            },
        )

    def test_templates_use_dedicated_root_and_defense_in_depth_denials(self):
        required = (
            "root /var/www/uplivion/public/;",
            r"location ~ (^|/)\.",
            r"\.(?:db|sqlite|sqlite3)",
            "ini|json|key",
            "docs|fail2ban|nginx-templates|tests|venv",
            "bak|old|orig|save|swp|tmp",
        )
        for template in TEMPLATES:
            config = template.read_text(encoding="utf-8")
            with self.subTest(template=template.name):
                for fragment in required:
                    self.assertIn(fragment, config)
                self.assertNotRegex(
                    config,
                    re.compile(r"^\s*root /var/www/uplivion/;\s*$", re.MULTILINE),
                )

    def test_known_sensitive_urls_are_not_public_assets(self):
        for url_path in (
            ".git/config",
            ".dev/gui_server.py",
            "create_users.py",
            "docs/CODE_SWEEP_2026-07-26.md",
            "uplivion.nginx",
            "requirements.txt",
            "server.py",
            "store.db",
        ):
            with self.subTest(url_path=url_path):
                self.assertFalse((PUBLIC / url_path).exists())
