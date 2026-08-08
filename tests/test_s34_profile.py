import importlib
import os
import sys
import tempfile
import unittest

from pathlib import Path

import bcrypt
import jwt


class ProfileTest(unittest.TestCase):
    """Self-service profile endpoints: name, own files, own account."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        os.environ["UPLIVION_UPLOAD_DIR"] = str(root / "share")
        os.environ["UPLIVION_DB_PATH"] = str(root / "store.db")
        os.environ["SECRET_KEY"] = "s" * 32
        os.environ["ACCESS_TOKEN_SECRET"] = "a" * 32
        sys.modules.pop("server", None)
        self.server = importlib.import_module("server")
        self.client = self.server.app.test_client()

        pw = bcrypt.hashpw(b"Admin-1234!", bcrypt.gensalt())
        rows = [
            ("super-id", "super", "superadmin", 53687091200),
            ("admin-id", "admin", "admin", 53687091200),
            ("user-id", "testuser", "user", 10737418240),
        ]
        with self.server.db_session() as conn:
            for user_id, username, role, quota in rows:
                conn.execute(
                    """
                    INSERT INTO users
                        (user_id, username, password_hash, role, quota_bytes, created)
                    VALUES (?, ?, ?, ?, ?, 'now')
                    """,
                    (user_id, username, pw, role, quota),
                )
            conn.commit()

        self.super_headers = {"Authorization": f"Bearer {self._token('super-id', 'super')}"}
        self.admin_headers = {"Authorization": f"Bearer {self._token('admin-id', 'admin')}"}
        self.user_headers = {"Authorization": f"Bearer {self._token('user-id', 'testuser')}"}

    def tearDown(self):
        sys.modules.pop("server", None)
        self.temp_dir.cleanup()

    def _token(self, user_id, username):
        return jwt.encode(
            {"user_id": user_id, "username": username, "ip": "127.0.0.1", "aver": 0},
            os.environ["ACCESS_TOKEN_SECRET"],
            algorithm="HS256",
        )

    def _add_file(self, user_id, suffix):
        share_dir = Path(os.environ["UPLIVION_UPLOAD_DIR"]) / user_id
        share_dir.mkdir(parents=True, exist_ok=True)
        (share_dir / suffix).write_bytes(b"data")
        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO links
                    (user_id, file_id, file_name, size, hash, uploaded,
                     linktoken, expires, created, revoked)
                VALUES (?, ?, ?, 4, '', '', ?, 9999999999, '', 0)
                """,
                (user_id, f"{user_id}/{suffix}", f"{suffix}.bin", f"tok-{suffix}"),
            )
            conn.commit()

    # --- name updates ---
    def test_update_own_name(self):
        res = self.client.post(
            "/profile", headers=self.user_headers,
            json={"first_name": "  Grace ", "last_name": "Hopper"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["first_name"], "Grace")  # stripped
        with self.server.db_session() as conn:
            row = conn.execute(
                "SELECT first_name, last_name FROM users WHERE user_id = 'user-id'"
            ).fetchone()
        self.assertEqual(row, ("Grace", "Hopper"))

    def test_update_name_can_clear(self):
        self.client.post(
            "/profile", headers=self.user_headers,
            json={"first_name": "Grace", "last_name": "Hopper"},
        )
        res = self.client.post("/profile", headers=self.user_headers, json={})
        self.assertEqual(res.status_code, 200)
        with self.server.db_session() as conn:
            row = conn.execute(
                "SELECT first_name, last_name FROM users WHERE user_id = 'user-id'"
            ).fetchone()
        self.assertEqual(row, ("", ""))

    def test_update_name_rejects_overlong(self):
        res = self.client.post(
            "/profile", headers=self.user_headers,
            json={"first_name": "x" * 51},
        )
        self.assertEqual(res.status_code, 400)

    def test_update_name_requires_auth(self):
        res = self.client.post("/profile", json={"first_name": "Nobody"})
        self.assertEqual(res.status_code, 401)

    # --- delete own files ---
    def test_delete_own_files_keeps_account(self):
        self._add_file("user-id", "keep")
        res = self.client.delete("/profile/files", headers=self.user_headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["deleted"], 1)
        with self.server.db_session() as conn:
            users = conn.execute(
                "SELECT COUNT(*) FROM users WHERE user_id = 'user-id'"
            ).fetchone()[0]
            links = conn.execute(
                "SELECT COUNT(*) FROM links WHERE user_id = 'user-id'"
            ).fetchone()[0]
        self.assertEqual(users, 1)
        self.assertEqual(links, 0)
        self.assertFalse((Path(os.environ["UPLIVION_UPLOAD_DIR"]) / "user-id").exists())

    def test_delete_own_files_leaves_other_users_alone(self):
        self._add_file("user-id", "mine")
        self._add_file("admin-id", "theirs")
        self.client.delete("/profile/files", headers=self.user_headers)
        with self.server.db_session() as conn:
            other = conn.execute(
                "SELECT COUNT(*) FROM links WHERE user_id = 'admin-id'"
            ).fetchone()[0]
        self.assertEqual(other, 1)

    # --- delete own account ---
    def test_delete_own_account(self):
        self._add_file("user-id", "gone")
        res = self.client.delete("/profile", headers=self.user_headers)
        self.assertEqual(res.status_code, 200)
        with self.server.db_session() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM users WHERE user_id = 'user-id'"
            ).fetchone()[0]
            links = conn.execute(
                "SELECT COUNT(*) FROM links WHERE user_id = 'user-id'"
            ).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(links, 0)
        self.assertFalse((Path(os.environ["UPLIVION_UPLOAD_DIR"]) / "user-id").exists())

    def test_admin_can_always_delete_own_account(self):
        # An admin is not the protected tier, so self-delete is unconditional
        # (a superadmin always remains to govern).
        res = self.client.delete("/profile", headers=self.admin_headers)
        self.assertEqual(res.status_code, 200)
        with self.server.db_session() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM users WHERE user_id = 'admin-id'"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_superadmin_can_delete_own_account_when_not_last(self):
        # A second superadmin means the first is no longer the last active one.
        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO users
                    (user_id, username, password_hash, role, quota_bytes, created)
                VALUES ('super2-id', 'super2', ?,
                        'superadmin', 53687091200, 'now')
                """,
                (bcrypt.hashpw(b"Admin-1234!", bcrypt.gensalt()),),
            )
            conn.commit()
        res = self.client.delete("/profile", headers=self.super_headers)
        self.assertEqual(res.status_code, 200)

    def test_last_active_superadmin_cannot_delete_own_account(self):
        res = self.client.delete("/profile", headers=self.super_headers)
        self.assertEqual(res.status_code, 400)
        self.assertIn("last", res.get_json()["error"].lower())
        with self.server.db_session() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM users WHERE user_id = 'super-id'"
            ).fetchone()[0]
        self.assertEqual(count, 1)  # still there


if __name__ == "__main__":
    unittest.main()
