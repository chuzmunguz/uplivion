import unittest

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AuthorizationDocumentationTest(unittest.TestCase):
    def test_readme_describes_three_role_model(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        normalized = " ".join(readme.split())
        self.assertIn("three", normalized)
        self.assertIn("role", normalized)
        self.assertIn("superadmin", normalized.lower())
        self.assertIn("admins", normalized.lower())
        self.assertIn("can create", normalized.lower())
        self.assertNotIn("production-grade", readme.lower())
        self.assertNotIn("background processing", readme.lower())

    def test_cli_prompts_for_authoritative_role(self):
        cli = (ROOT / "create_users.py").read_text(encoding="utf-8")
        self.assertIn("Enter role", cli)
        self.assertIn("role,", cli)

    def test_schema_enforces_role_and_status_constraints(self):
        schema = (ROOT / "store_schema.py").read_text(encoding="utf-8")
        self.assertIn("role TEXT NOT NULL DEFAULT 'user'", schema)
        self.assertIn("CHECK (role IN ('superadmin', 'admin', 'user'))", schema)
        self.assertIn("status TEXT NOT NULL DEFAULT 'active'", schema)
        self.assertIn("CHECK (status IN ('active', 'disabled'))", schema)
