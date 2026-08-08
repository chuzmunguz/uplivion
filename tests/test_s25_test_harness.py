import os
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestHarnessContractTest(unittest.TestCase):
    def test_obsolete_external_scripts_and_fixture_are_removed(self):
        self.assertFalse((ROOT / ".dev" / "testupload_ordered.py").exists())
        self.assertFalse((ROOT / ".dev" / "testupload_random.py").exists())
        self.assertFalse((ROOT / ".dev" / "testfile.zip").exists())

    def test_single_runner_is_executable_and_discovers_every_finding_test(self):
        runner = ROOT / "tests" / "run.sh"
        self.assertTrue(os.access(runner, os.X_OK))
        contents = runner.read_text(encoding="utf-8")
        self.assertIn("-m unittest discover", contents)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", contents)

        tests = {path.name for path in (ROOT / "tests").glob("test_s*.py")}
        for finding in range(1, 26):
            with self.subTest(finding=finding):
                self.assertTrue(
                    any(
                        name.startswith(f"test_s{finding:02d}_")
                        for name in tests
                    )
                )
