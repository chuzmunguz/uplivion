import stat
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryAccessibilityTest(unittest.TestCase):
    def test_only_operator_shell_scripts_are_executable(self):
        executable = set()
        for path in ROOT.rglob("*"):
            if path.is_file() and ".git" not in path.parts:
                if path.stat().st_mode & stat.S_IXUSR:
                    executable.add(str(path.relative_to(ROOT)))
        self.assertEqual(
            executable,
            {
                "create_user.sh",
                "install.sh",
                "seed.sh",
                "tests/run.sh",
                "uninstall.sh",
            },
        )

    def test_runtime_secrets_state_and_caches_are_ignored(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in (
            "/*.env",
            "/store.db",
            "/store.db-*",
            "/share/",
            "/venv/",
            "__pycache__/",
            ".ruff_cache/",
        ):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, ignore)

    def test_keyboard_and_zoom_contracts_are_present(self):
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "public" / "style.css").read_text(encoding="utf-8")
        javascript = (ROOT / "public" / "uplivion.js").read_text(encoding="utf-8")

        self.assertIn(
            'content="width=device-width, initial-scale=1', html
        )
        self.assertNotIn("user-scalable=no", html)
        self.assertNotIn("maximum-scale=1", html)
        self.assertIn('<button type="button" id="profile-btn">', html)
        self.assertIn('<button type="button" id="logout">', html)
        self.assertEqual(html.count('type="button" class="eye-container"'), 3)
        self.assertGreaterEqual(html.count('aria-label="Show '), 3)
        self.assertIn(":focus-visible {", css)
        self.assertIn("outline: 3px solid", css)
        self.assertNotIn("\n:focus {\n", css)
        self.assertIn('"aria-expanded",', javascript)
        self.assertFalse((ROOT / ".dev" / "testfile.zip").exists())
