import ast
import re
import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def strip_comments(text):
    """Drop comment lines so prose (which mentions 'location',
    'proxy_set_header') never confuses directive-ordering checks."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


class DeadCodeTest(unittest.TestCase):
    def test_backend_dead_imports_executor_and_hash_function_are_removed(self):
        server_source = (ROOT / "server.py").read_text(encoding="utf-8")
        tree = ast.parse(server_source)
        imported = set()
        functions = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.add(node.name)

        self.assertTrue({"SimpleCookie", "Lock", "ThreadPoolExecutor"}.isdisjoint(imported))
        self.assertNotIn("compute_file_hash", functions)
        self.assertNotIn("executor =", server_source)
        for path in (ROOT / "tests").glob("test_*.py"):
            if path == Path(__file__):
                continue
            with self.subTest(path=path.name):
                self.assertNotIn(
                    ".executor.shutdown", path.read_text(encoding="utf-8")
                )

    def test_frontend_and_styles_have_one_live_form_of_each_item(self):
        javascript = (ROOT / "public" / "uplivion.js").read_text(encoding="utf-8")
        html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "public" / "style.css").read_text(encoding="utf-8")

        self.assertNotIn("existingFileSize", javascript)
        self.assertNotIn("let totalSize", javascript)
        self.assertNotIn("let burgerMenu", javascript)
        self.assertNotRegex(html, r'id="upload"[^>]*\bmultiple\b')
        self.assertEqual(len(re.findall(r"^\.file-details\s*\{", css, re.MULTILINE)), 1)
        self.assertEqual(len(re.findall(r"^body\.modal-open\s*\{", css, re.MULTILINE)), 1)
        self.assertEqual(
            len(
                re.findall(
                    r"^#change-password-modal #password-rules\s*\{",
                    css,
                    re.MULTILINE,
                )
            ),
            1,
        )
        self.assertNotIn(".copy-link-btn", css)
        self.assertNotIn(".delete-link-btn", css)
        self.assertNotIn("#change-password-modal .overwrite-", css)

    def test_retired_proxy_forwarding_snippet_leaves_no_references(self):
        # D-11 folded the per-location forwarding snippet into server-scope
        # proxy_set_header declarations and deleted uplivion-proxy-forwarding.conf.
        # No file, template, installer, or uninstaller may still reference it.
        self.assertFalse(
            (ROOT / "uplivion-proxy-forwarding.conf").exists()
        )
        for rel in (
            "uplivion.nginx",
            "install.sh",
            "uninstall.sh",
        ):
            with self.subTest(path=rel):
                self.assertNotIn(
                    "uplivion-proxy-forwarding",
                    (ROOT / rel).read_text(encoding="utf-8"),
                )
        # The identity headers survive as one server-scope primitive per
        # proxying server block, inherited by every location.
        config = (ROOT / "uplivion.nginx").read_text(encoding="utf-8")
        proxying = [
            block for block in config.split("server {")[1:] if "proxy_pass" in block
        ]
        self.assertEqual(len(proxying), 2)
        for block in proxying:
            with self.subTest(block=block[:48]):
                self.assertEqual(
                    block.count("proxy_set_header X-Forwarded-For"), 1
                )
                code = strip_comments(block)
                self.assertLess(
                    code.index("proxy_set_header"), code.index("location")
                )
