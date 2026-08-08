import subprocess
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendAuthContractTest(unittest.TestCase):
    def test_refresh_coordinator_executes_with_serial_rotation_and_upload_retry(self):
        subprocess.run(
            ["node", str(ROOT / "tests" / "frontend_auth_test.mjs")],
            cwd=ROOT,
            check=True,
        )

    def test_overwrite_conflict_never_becomes_a_resume_identifier(self):
        subprocess.run(
            ["node", str(ROOT / "tests" / "frontend_overwrite_test.mjs")],
            cwd=ROOT,
            check=True,
        )

    def test_lifecycle_source_contracts(self):
        javascript = (ROOT / "public" / "uplivion.js").read_text(encoding="utf-8")
        self.assertTrue(javascript.startswith('"use strict";'))
        self.assertIn("let burgerIcon;", javascript)
        self.assertIn("let progressBarText;", javascript)
        self.assertIn("let refreshPromise = null;", javascript)
        self.assertIn('fetchProtectedResponse("/upload"', javascript)
        self.assertIn('if (err.name === "AbortError") throw err;', javascript)
        self.assertIn("okBtn.onclick = okHandler;", javascript)
        self.assertIn("cancelBtn.onclick = cancelHandler;", javascript)
        self.assertIn("overlay.onclick = overlayHandler;", javascript)
        self.assertNotIn("localStorage", javascript)
        self.assertNotIn("existingFileSize", javascript)
        self.assertNotIn("let totalSize", javascript)
        self.assertNotIn("let burgerMenu", javascript)
