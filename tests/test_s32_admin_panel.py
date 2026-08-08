import importlib
import io
import os
import sys
import tempfile
import unittest

from pathlib import Path

import bcrypt
import jwt


class AdminPanelTest(unittest.TestCase):
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
        # Roles: superadmin > admin > user. The superadmin is the CLI-rooted top
        # tier; the admin is a web-manageable middle tier that governs only users.
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

    def _token(self, user_id, username):
        return jwt.encode(
            {"user_id": user_id, "username": username, "ip": "127.0.0.1", "aver": 0},
            os.environ["ACCESS_TOKEN_SECRET"],
            algorithm="HS256",
        )

    def tearDown(self):
        sys.modules.pop("server", None)
        self.temp_dir.cleanup()

    def test_list_users(self):
        # Both admins and superadmins see every account (higher tiers render
        # read-only client-side); the roster itself is not filtered.
        res = self.client.get("/admin/users", headers=self.admin_headers)
        self.assertEqual(res.status_code, 200)
        users = res.get_json()
        self.assertEqual(len(users), 3)
        names = {u["username"] for u in users}
        self.assertEqual(names, {"super", "admin", "testuser"})

    def test_non_admin_cannot_list_users(self):
        res = self.client.get("/admin/users", headers=self.user_headers)
        self.assertEqual(res.status_code, 403)

    def test_create_user(self):
        res = self.client.post(
            "/admin/users",
            headers=self.admin_headers,
            json={"username": "newuser", "password": "New-1234!", "role": "user", "quota_bytes": 5},
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("user_id", res.get_json())

        with self.server.db_session() as conn:
            row = conn.execute(
                "SELECT role, quota_bytes FROM users WHERE username = 'newuser'"
            ).fetchone()
        self.assertEqual(row, ("user", 5))

    def test_create_user_with_names_stored_and_listed(self):
        res = self.client.post(
            "/admin/users",
            headers=self.admin_headers,
            json={
                "username": "nameduser",
                "password": "New-1234!",
                "role": "user",
                "quota_bytes": 5,
                "first_name": "  Grace  ",
                "last_name": "Hopper",
            },
        )
        self.assertEqual(res.status_code, 200)
        with self.server.db_session() as conn:
            row = conn.execute(
                "SELECT first_name, last_name FROM users WHERE username = 'nameduser'"
            ).fetchone()
        self.assertEqual(row, ("Grace", "Hopper"))  # stripped

        users = self.client.get("/admin/users", headers=self.admin_headers).get_json()
        named = next(u for u in users if u["username"] == "nameduser")
        self.assertEqual(named["first_name"], "Grace")
        self.assertEqual(named["last_name"], "Hopper")

    def test_create_user_defaults_names_to_empty(self):
        res = self.client.post(
            "/admin/users",
            headers=self.admin_headers,
            json={"username": "noname", "password": "New-1234!", "role": "user", "quota_bytes": 5},
        )
        self.assertEqual(res.status_code, 200)
        users = self.client.get("/admin/users", headers=self.admin_headers).get_json()
        noname = next(u for u in users if u["username"] == "noname")
        self.assertEqual(noname["first_name"], "")
        self.assertEqual(noname["last_name"], "")

    def test_create_user_rejects_overlong_name(self):
        res = self.client.post(
            "/admin/users",
            headers=self.admin_headers,
            json={
                "username": "longname",
                "password": "New-1234!",
                "role": "user",
                "quota_bytes": 5,
                "first_name": "x" * 51,
            },
        )
        self.assertEqual(res.status_code, 400)

    def test_list_users_reports_file_count(self):
        with self.server.db_session() as conn:
            for i in range(3):
                conn.execute(
                    """
                    INSERT INTO links
                        (user_id, file_id, file_name, size, hash, uploaded,
                         linktoken, expires, created, revoked)
                    VALUES ('user-id', ?, ?, 4, '', '', ?, 9999999999, '', 0)
                    """,
                    (f"user-id/f{i}", f"file{i}.bin", f"tok{i}"),
                )
            conn.commit()

        users = self.client.get("/admin/users", headers=self.admin_headers).get_json()
        testuser = next(u for u in users if u["username"] == "testuser")
        admin = next(u for u in users if u["username"] == "admin")
        self.assertEqual(testuser["file_count"], 3)
        self.assertEqual(admin["file_count"], 0)

    def test_create_duplicate_username(self):
        res = self.client.post(
            "/admin/users",
            headers=self.admin_headers,
            json={"username": "testuser", "password": "New-1234!", "role": "user", "quota_bytes": 5},
        )
        self.assertEqual(res.status_code, 409)

    def test_delete_user_cascades_files(self):
        share_dir = Path(os.environ["UPLIVION_UPLOAD_DIR"]) / "user-id"
        share_dir.mkdir(parents=True)
        test_file = share_dir / "somefile"
        test_file.write_bytes(b"data")

        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO links
                    (user_id, file_id, file_name, size, hash, uploaded,
                     linktoken, expires, created, revoked)
                VALUES ('user-id', 'user-id/somefile', 'test.bin', 4, '', '',
                        'tok', 9999999999, '', 0)
                """
            )
            conn.commit()

        res = self.client.delete(
            "/admin/users/user-id", headers=self.admin_headers
        )
        self.assertEqual(res.status_code, 200)

        with self.server.db_session() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM users WHERE user_id = 'user-id'"
            ).fetchone()[0]
        self.assertEqual(count, 0)
        self.assertFalse(share_dir.exists())

    def test_delete_all_files_keeps_user(self):
        share_dir = Path(os.environ["UPLIVION_UPLOAD_DIR"]) / "user-id"
        share_dir.mkdir(parents=True)
        (share_dir / "somefile").write_bytes(b"data")

        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO links
                    (user_id, file_id, file_name, size, hash, uploaded,
                     linktoken, expires, created, revoked)
                VALUES ('user-id', 'user-id/somefile', 'test.bin', 4, '', '',
                        'tok', 9999999999, '', 0)
                """
            )
            conn.execute(
                """
                INSERT INTO quota_reservations (user_id, file_id, size, created_at)
                VALUES ('user-id', 'user-id/pending.part', 10, 1)
                """
            )
            conn.commit()

        res = self.client.delete(
            "/admin/users/user-id/files", headers=self.admin_headers
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["deleted"], 1)

        with self.server.db_session() as conn:
            user_count = conn.execute(
                "SELECT COUNT(*) FROM users WHERE user_id = 'user-id'"
            ).fetchone()[0]
            link_count = conn.execute(
                "SELECT COUNT(*) FROM links WHERE user_id = 'user-id'"
            ).fetchone()[0]
            reservation_count = conn.execute(
                "SELECT COUNT(*) FROM quota_reservations WHERE user_id = 'user-id'"
            ).fetchone()[0]
        self.assertEqual(user_count, 1)      # account survives
        self.assertEqual(link_count, 0)      # files gone
        self.assertEqual(reservation_count, 0)
        self.assertFalse(share_dir.exists())

    def test_delete_files_nonexistent_user(self):
        res = self.client.delete(
            "/admin/users/no-such-id/files", headers=self.admin_headers
        )
        self.assertEqual(res.status_code, 404)

    def test_non_admin_cannot_delete_files(self):
        res = self.client.delete(
            "/admin/users/user-id/files", headers=self.user_headers
        )
        self.assertEqual(res.status_code, 403)

    def test_cannot_delete_self(self):
        res = self.client.delete(
            "/admin/users/admin-id", headers=self.admin_headers
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("yourself", res.get_json()["error"])

    def test_disable_user_bumps_auth_version(self):
        with self.server.db_session() as conn:
            before = conn.execute(
                "SELECT auth_version FROM users WHERE user_id = 'user-id'"
            ).fetchone()[0]

        res = self.client.post(
            "/admin/users/user-id/status",
            headers=self.admin_headers,
            json={"status": "disabled"},
        )
        self.assertEqual(res.status_code, 200)

        with self.server.db_session() as conn:
            after = conn.execute(
                "SELECT auth_version, status FROM users WHERE user_id = 'user-id'"
            ).fetchone()
        self.assertEqual(after[1], "disabled")
        self.assertGreater(after[0], before)

    def test_cannot_disable_last_superadmin(self):
        # Only one superadmin exists, so it cannot be disabled away.
        res = self.client.post(
            "/admin/users/super-id/status",
            headers=self.super_headers,
            json={"status": "disabled"},
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("last", res.get_json()["error"].lower())

    def test_superadmin_can_change_role(self):
        res = self.client.post(
            "/admin/users/user-id/role",
            headers=self.super_headers,
            json={"role": "admin"},
        )
        self.assertEqual(res.status_code, 200)
        with self.server.db_session() as conn:
            role = conn.execute(
                "SELECT role FROM users WHERE user_id = 'user-id'"
            ).fetchone()[0]
        self.assertEqual(role, "admin")

        # And can demote the same account back down to a regular user.
        res = self.client.post(
            "/admin/users/user-id/role",
            headers=self.super_headers,
            json={"role": "user"},
        )
        self.assertEqual(res.status_code, 200)

    def test_admin_cannot_change_role(self):
        res = self.client.post(
            "/admin/users/user-id/role",
            headers=self.admin_headers,
            json={"role": "admin"},
        )
        self.assertEqual(res.status_code, 403)
        with self.server.db_session() as conn:
            role = conn.execute(
                "SELECT role FROM users WHERE user_id = 'user-id'"
            ).fetchone()[0]
        self.assertEqual(role, "user")  # unchanged

    def test_role_endpoint_rejects_superadmin_value(self):
        res = self.client.post(
            "/admin/users/user-id/role",
            headers=self.super_headers,
            json={"role": "superadmin"},
        )
        self.assertEqual(res.status_code, 400)

    def test_role_endpoint_cannot_touch_a_superadmin(self):
        res = self.client.post(
            "/admin/users/super-id/role",
            headers=self.super_headers,
            json={"role": "admin"},
        )
        self.assertEqual(res.status_code, 403)
        with self.server.db_session() as conn:
            role = conn.execute(
                "SELECT role FROM users WHERE user_id = 'super-id'"
            ).fetchone()[0]
        self.assertEqual(role, "superadmin")  # unchanged

    # --- tier governance (superadmin > admin > user) ---

    def _all_mutations_on(self, target_id, headers):
        """Every selection-dependent admin mutation, keyed by name."""
        return {
            "status": self.client.post(
                f"/admin/users/{target_id}/status",
                headers=headers, json={"status": "disabled"},
            ),
            "quota": self.client.post(
                f"/admin/users/{target_id}/quota",
                headers=headers, json={"quota_bytes": 1024},
            ),
            "password": self.client.post(
                f"/admin/users/{target_id}/password",
                headers=headers, json={"password": "Reset-5678!"},
            ),
            "files": self.client.delete(
                f"/admin/users/{target_id}/files", headers=headers,
            ),
            "delete": self.client.delete(
                f"/admin/users/{target_id}", headers=headers,
            ),
        }

    def _add_superadmin(self, user_id, username):
        pw = bcrypt.hashpw(b"Admin-1234!", bcrypt.gensalt())
        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO users
                    (user_id, username, password_hash, role, quota_bytes, created)
                VALUES (?, ?, ?, 'superadmin', 53687091200, 'now')
                """,
                (user_id, username, pw),
            )
            conn.commit()

    def test_admin_cannot_manage_a_higher_tier(self):
        # A regular admin is refused every management action on a superadmin.
        for action, res in self._all_mutations_on("super-id", self.admin_headers).items():
            with self.subTest(action=action):
                self.assertEqual(res.status_code, 403)

    def test_superadmin_can_manage_an_admin(self):
        for action, res in self._all_mutations_on("admin-id", self.super_headers).items():
            with self.subTest(action=action):
                self.assertEqual(res.status_code, 200)

    def test_superadmin_can_manage_another_superadmin(self):
        # Q2: superadmins manage one another; a second keeps neither "last".
        self._add_superadmin("super2-id", "super2")
        disable = self.client.post(
            "/admin/users/super2-id/status",
            headers=self.super_headers, json={"status": "disabled"},
        )
        self.assertEqual(disable.status_code, 200)
        delete = self.client.delete(
            "/admin/users/super2-id", headers=self.super_headers,
        )
        self.assertEqual(delete.status_code, 200)

    def test_admin_cannot_create_admin(self):
        res = self.client.post(
            "/admin/users",
            headers=self.admin_headers,
            json={"username": "wannabe", "password": "New-1234!", "role": "admin", "quota_bytes": 5},
        )
        self.assertEqual(res.status_code, 403)
        with self.server.db_session() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM users WHERE username = 'wannabe'"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_superadmin_can_create_admin(self):
        res = self.client.post(
            "/admin/users",
            headers=self.super_headers,
            json={"username": "newadmin", "password": "New-1234!", "role": "admin", "quota_bytes": 5},
        )
        self.assertEqual(res.status_code, 200)
        with self.server.db_session() as conn:
            role = conn.execute(
                "SELECT role FROM users WHERE username = 'newadmin'"
            ).fetchone()[0]
        self.assertEqual(role, "admin")

    def test_creating_a_superadmin_is_rejected(self):
        # The top tier is CLI-only — not grantable over the web even by a super.
        res = self.client.post(
            "/admin/users",
            headers=self.super_headers,
            json={"username": "root2", "password": "New-1234!", "role": "superadmin", "quota_bytes": 5},
        )
        self.assertEqual(res.status_code, 400)

    def test_reset_password(self):
        res = self.client.post(
            "/admin/users/user-id/password",
            headers=self.admin_headers,
            json={"password": "Reset-5678!"},
        )
        self.assertEqual(res.status_code, 200)
        with self.server.db_session() as conn:
            pw_hash = conn.execute(
                "SELECT password_hash FROM users WHERE user_id = 'user-id'"
            ).fetchone()[0]
        self.assertTrue(bcrypt.checkpw(b"Reset-5678!", pw_hash))

    def test_set_quota(self):
        res = self.client.post(
            "/admin/users/user-id/quota",
            headers=self.admin_headers,
            json={"quota_bytes": 2 * 1024 ** 3},
        )
        self.assertEqual(res.status_code, 200)
        with self.server.db_session() as conn:
            quota = conn.execute(
                "SELECT quota_bytes FROM users WHERE user_id = 'user-id'"
            ).fetchone()[0]
        self.assertEqual(quota, 2 * 1024 ** 3)

    def test_set_quota_rejects_non_positive_and_non_integer(self):
        for bad in (0, -1, 1.5, "10", None, True):
            res = self.client.post(
                "/admin/users/user-id/quota",
                headers=self.admin_headers,
                json={"quota_bytes": bad},
            )
            with self.subTest(bad=bad):
                self.assertEqual(res.status_code, 400)

    def test_create_user_rejects_invalid_quota(self):
        for bad in (0, -5, 2.5, "1", None):
            res = self.client.post(
                "/admin/users",
                headers=self.admin_headers,
                json={"username": "q", "password": "New-1234!", "role": "user", "quota_bytes": bad},
            )
            with self.subTest(bad=bad):
                self.assertEqual(res.status_code, 400)

    def test_disabled_user_cannot_login(self):
        with self.server.db_session() as conn:
            conn.execute(
                "UPDATE users SET status = 'disabled' WHERE user_id = 'user-id'"
            )
            conn.commit()

        res = self.client.post(
            "/login",
            json={"username": "testuser", "password": "Admin-1234!"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("disabled", res.get_json()["error"].lower())

    def test_check_returns_role(self):
        res = self.client.post("/check", headers=self.admin_headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["role"], "admin")

        res = self.client.post("/check", headers=self.user_headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["role"], "user")

        res = self.client.post("/check", headers=self.super_headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["role"], "superadmin")

    def test_check_returns_identity(self):
        with self.server.db_session() as conn:
            conn.execute(
                "UPDATE users SET first_name = 'Test', last_name = 'User' "
                "WHERE user_id = 'user-id'"
            )
            conn.commit()
        body = self.client.post("/check", headers=self.user_headers).get_json()
        self.assertEqual(body["user_id"], "user-id")
        self.assertEqual(body["username"], "testuser")
        self.assertEqual(body["first_name"], "Test")
        self.assertEqual(body["last_name"], "User")

    def test_delete_nonexistent_user(self):
        res = self.client.delete(
            "/admin/users/no-such-id", headers=self.admin_headers
        )
        self.assertEqual(res.status_code, 404)

    def test_enable_disabled_user(self):
        with self.server.db_session() as conn:
            conn.execute(
                "UPDATE users SET status = 'disabled' WHERE user_id = 'user-id'"
            )
            conn.commit()

        res = self.client.post(
            "/admin/users/user-id/status",
            headers=self.admin_headers,
            json={"status": "active"},
        )
        self.assertEqual(res.status_code, 200)

        with self.server.db_session() as conn:
            status = conn.execute(
                "SELECT status FROM users WHERE user_id = 'user-id'"
            ).fetchone()[0]
        self.assertEqual(status, "active")
