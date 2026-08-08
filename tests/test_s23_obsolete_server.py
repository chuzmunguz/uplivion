import subprocess
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ObsoleteServerTest(unittest.TestCase):
    def test_obsolete_autostart_server_and_embedded_secret_are_absent(self):
        self.assertFalse((ROOT / ".dev" / "gui_server.py").exists())
        secret = (
            "673df101b844cb23bc26d4ec02f709814"
            "d985e6d7cccea3d7896d36c60d5e407"
        )
        tracked = subprocess.check_output(
            ["git", "ls-files"], cwd=ROOT, text=True
        ).splitlines()
        for relative in tracked:
            path = ROOT / relative
            if (
                path.is_file()
                and path.name != "CODE_SWEEP_2026-07-26.md"
            ):
                with self.subTest(path=path.relative_to(ROOT)):
                    self.assertNotIn(
                        secret.encode(), path.read_bytes()
                    )
