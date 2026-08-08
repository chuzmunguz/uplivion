import hashlib
import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DIGEST = (
    "0140aee1d6043da76c6c7e5089947e65"
    "c8151d33c92d5b1909af2e8db1c101a7"
)


class DependencyLockTest(unittest.TestCase):
    def test_runtime_lock_is_exact_hashed_and_contains_no_test_or_dead_deps(self):
        lock_path = ROOT / "requirements.txt"
        lock = lock_path.read_text(encoding="utf-8")
        digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()
        self.assertEqual(digest, EXPECTED_DIGEST)

        packages = {
            match.group(1).lower()
            for match in re.finditer(r"^([A-Za-z0-9_-]+)==", lock, re.MULTILINE)
        }
        self.assertEqual(
            packages,
            {
                "bcrypt",
                "blinker",
                "click",
                "flask",
                "gunicorn",
                "itsdangerous",
                "jinja2",
                "markupsafe",
                "packaging",
                "pyjwt",
                "werkzeug",
            },
        )
        self.assertNotIn("requests", packages)
        self.assertNotIn("pytest", packages)
        requirement_lines = [
            line
            for line in lock.splitlines()
            if line and not line.startswith((" ", "#"))
        ]
        self.assertTrue(all("==" in line for line in requirement_lines))
        self.assertGreaterEqual(lock.count("--hash=sha256:"), len(packages))

    def test_test_lock_reuses_runtime_and_installer_requires_hashes(self):
        test_lock = (ROOT / "requirements-test.txt").read_text(
            encoding="utf-8"
        )
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        record = (ROOT / "DEPENDENCY_LOCK.md").read_text(encoding="utf-8")
        self.assertEqual(
            test_lock.splitlines(), ["--require-hashes", "-r requirements.txt"]
        )
        self.assertIn("pip\" install --require-hashes", installer)
        self.assertIn(EXPECTED_DIGEST, record)
        self.assertIn("Python 3.13.5", record)
