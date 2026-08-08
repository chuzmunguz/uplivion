import base64
import hashlib
import hmac
import importlib
import io
import os
import sys
import tempfile
import time
import unittest
import uuid

from pathlib import Path

import jwt


class LinkPrivacyTest(unittest.TestCase):
    """Download-count limits, the per-file settings/revoke/delete endpoints, and
    the owner-scoping around them."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.upload_dir = root / "share"
        os.environ["UPLIVION_UPLOAD_DIR"] = str(self.upload_dir)
        os.environ["UPLIVION_DB_PATH"] = str(root / "store.db")
        os.environ["SECRET_KEY"] = "s" * 32
        os.environ["ACCESS_TOKEN_SECRET"] = "a" * 32
        sys.modules.pop("server", None)
        self.server = importlib.import_module("server")
        self.client = self.server.app.test_client()

    def tearDown(self):
        sys.modules.pop("server", None)
        self.temp_dir.cleanup()

    # --- helpers ---

    def token_for(self, file_id, expires):
        digest = hmac.new(
            os.environ["SECRET_KEY"].encode(),
            f"{file_id}:{expires}".encode(),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def add_user(self, user_id):
        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO users
                    (user_id, username, password_hash, quota_bytes, created)
                VALUES (?, ?, X'00', 1073741824, 'now')
                """,
                (user_id, f"user_{user_id[:8]}"),
            )
            conn.commit()

    def add_link(self, user_id, max_downloads=None, download_count=0, expires_in=3600):
        file_id = f"{user_id}/{uuid.uuid4()}"
        expires = int(time.time()) + expires_in
        token = self.token_for(file_id, expires)
        (self.upload_dir / user_id).mkdir(parents=True, exist_ok=True)
        (self.upload_dir / file_id).write_bytes(b"content")
        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO links
                    (user_id, file_id, file_name, size, hash, uploaded,
                     linktoken, expires, created, revoked,
                     max_downloads, download_count)
                VALUES (?, ?, 'file.txt', 7, '', 'now', ?, ?, 'now', 0, ?, ?)
                """,
                (user_id, file_id, token, expires, max_downloads, download_count),
            )
            conn.commit()
        return file_id, token

    def headers_for(self, user_id):
        access = jwt.encode(
            {"user_id": user_id, "username": f"user_{user_id[:8]}",
             "ip": "127.0.0.1", "aver": 0},
            os.environ["ACCESS_TOKEN_SECRET"],
            algorithm="HS256",
        )
        return {"Authorization": f"Bearer {access}"}

    def download_count(self, file_id):
        with self.server.db_session() as conn:
            row = conn.execute(
                "SELECT download_count FROM links WHERE file_id = ?", (file_id,)
            ).fetchone()
        return row[0]

    # --- download counting ---

    def test_each_successful_serve_increments_the_download_count(self):
        user_id = str(uuid.uuid4())
        self.add_user(user_id)
        file_id, token = self.add_link(user_id)

        self.assertEqual(self.download_count(file_id), 0)
        for expected in (1, 2, 3):
            self.assertEqual(self.client.get(f"/share/{token}").status_code, 200)
            self.assertEqual(self.download_count(file_id), expected)

    def test_max_downloads_serves_up_to_the_limit_then_is_gone(self):
        user_id = str(uuid.uuid4())
        self.add_user(user_id)
        file_id, token = self.add_link(user_id, max_downloads=2)

        self.assertEqual(self.client.get(f"/share/{token}").status_code, 200)
        self.assertEqual(self.client.get(f"/share/{token}").status_code, 200)
        # The limit is reached; further requests are treated as gone and the
        # counter stops climbing.
        self.assertEqual(self.client.get(f"/share/{token}").status_code, 404)
        self.assertEqual(self.download_count(file_id), 2)

    def test_a_failed_serve_does_not_count(self):
        user_id = str(uuid.uuid4())
        self.add_user(user_id)
        file_id, token = self.add_link(user_id)
        # Tamper with the token so the HMAC compare fails.
        self.assertEqual(self.client.get(f"/share/{token}x").status_code, 404)
        self.assertEqual(self.download_count(file_id), 0)

    # --- listing ---

    def test_links_listing_exposes_the_new_fields(self):
        user_id = str(uuid.uuid4())
        self.add_user(user_id)
        file_id, _ = self.add_link(user_id, max_downloads=5, download_count=1)

        data = self.client.get("/links", headers=self.headers_for(user_id)).get_json()
        self.assertEqual(len(data), 1)
        row = data[0]
        self.assertEqual(row["file_id"], file_id)
        self.assertEqual(row["download_count"], 1)
        self.assertEqual(row["max_downloads"], 5)

    # --- per-file detail + owner scoping ---

    def test_detail_is_owner_scoped(self):
        owner = str(uuid.uuid4())
        other = str(uuid.uuid4())
        self.add_user(owner)
        self.add_user(other)
        file_id, _ = self.add_link(owner, max_downloads=3, download_count=2)

        ok = self.client.get(f"/links/{file_id}", headers=self.headers_for(owner))
        self.assertEqual(ok.status_code, 200)
        body = ok.get_json()
        self.assertEqual(body["download_count"], 2)
        self.assertEqual(body["max_downloads"], 3)

        # Another authenticated user cannot address someone else's file.
        denied = self.client.get(f"/links/{file_id}", headers=self.headers_for(other))
        self.assertEqual(denied.status_code, 404)

    # --- settings: expiry renewal + max_downloads ---

    def test_settings_renew_expiry_rotates_the_token(self):
        user_id = str(uuid.uuid4())
        self.add_user(user_id)
        file_id, old_token = self.add_link(user_id)

        resp = self.client.post(
            f"/links/{file_id}/settings",
            headers=self.headers_for(user_id),
            json={"expiry": 7200},
        )
        self.assertEqual(resp.status_code, 200)
        new_token = resp.get_json()["link"].rsplit("/", 1)[1]
        self.assertNotEqual(new_token, old_token)

        # The old token is dead; the new one serves.
        self.assertEqual(self.client.get(f"/share/{old_token}").status_code, 404)
        self.assertEqual(self.client.get(f"/share/{new_token}").status_code, 200)

    def test_settings_renew_clears_revoked(self):
        user_id = str(uuid.uuid4())
        self.add_user(user_id)
        file_id, token = self.add_link(user_id)
        self.client.post(f"/links/{file_id}/revoke", headers=self.headers_for(user_id))

        resp = self.client.post(
            f"/links/{file_id}/settings",
            headers=self.headers_for(user_id),
            json={"expiry": 3600},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()["revoked"])

    def test_settings_set_and_clear_max_downloads(self):
        user_id = str(uuid.uuid4())
        self.add_user(user_id)
        file_id, _ = self.add_link(user_id)
        H = self.headers_for(user_id)

        set_resp = self.client.post(
            f"/links/{file_id}/settings", headers=H, json={"max_downloads": 4}
        )
        self.assertEqual(set_resp.get_json()["max_downloads"], 4)

        clear_resp = self.client.post(
            f"/links/{file_id}/settings", headers=H, json={"max_downloads": None}
        )
        self.assertIsNone(clear_resp.get_json()["max_downloads"])

    def test_settings_reject_non_positive_max_downloads(self):
        user_id = str(uuid.uuid4())
        self.add_user(user_id)
        file_id, _ = self.add_link(user_id)
        resp = self.client.post(
            f"/links/{file_id}/settings",
            headers=self.headers_for(user_id),
            json={"max_downloads": 0},
        )
        self.assertEqual(resp.status_code, 400)

    def test_max_downloads_below_current_count_is_accepted_and_exhausts(self):
        user_id = str(uuid.uuid4())
        self.add_user(user_id)
        # Already served 3 times.
        file_id, token = self.add_link(user_id, download_count=3)

        resp = self.client.post(
            f"/links/{file_id}/settings",
            headers=self.headers_for(user_id),
            json={"max_downloads": 2},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["max_downloads"], 2)
        # 3 >= 2, so the link is immediately exhausted.
        self.assertEqual(self.client.get(f"/share/{token}").status_code, 404)

    def test_settings_are_owner_scoped(self):
        owner = str(uuid.uuid4())
        other = str(uuid.uuid4())
        self.add_user(owner)
        self.add_user(other)
        file_id, _ = self.add_link(owner)
        resp = self.client.post(
            f"/links/{file_id}/settings",
            headers=self.headers_for(other),
            json={"max_downloads": 2},
        )
        self.assertEqual(resp.status_code, 404)

    # --- revoke + delete ---

    def test_revoke_disables_the_link(self):
        user_id = str(uuid.uuid4())
        self.add_user(user_id)
        file_id, token = self.add_link(user_id)

        resp = self.client.post(f"/links/{file_id}/revoke", headers=self.headers_for(user_id))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["revoked"])
        self.assertEqual(self.client.get(f"/share/{token}").status_code, 404)

    def test_delete_removes_row_and_disk_bytes(self):
        user_id = str(uuid.uuid4())
        self.add_user(user_id)
        file_id, _ = self.add_link(user_id)
        disk_path = self.upload_dir / file_id
        self.assertTrue(disk_path.exists())

        resp = self.client.delete(f"/links/{file_id}", headers=self.headers_for(user_id))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(disk_path.exists())
        with self.server.db_session() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM links WHERE file_id = ?", (file_id,)
            ).fetchone()
        self.assertEqual(row[0], 0)
        # A second delete finds nothing.
        again = self.client.delete(f"/links/{file_id}", headers=self.headers_for(user_id))
        self.assertEqual(again.status_code, 404)

    def test_delete_is_owner_scoped(self):
        owner = str(uuid.uuid4())
        other = str(uuid.uuid4())
        self.add_user(owner)
        self.add_user(other)
        file_id, _ = self.add_link(owner)
        resp = self.client.delete(f"/links/{file_id}", headers=self.headers_for(other))
        self.assertEqual(resp.status_code, 404)
        # The owner's file survives an unauthorized delete attempt.
        self.assertTrue((self.upload_dir / file_id).exists())

    # --- notes ---

    def test_notes_set_reflected_in_list_and_detail_then_cleared(self):
        user_id = str(uuid.uuid4())
        self.add_user(user_id)
        file_id, _ = self.add_link(user_id)
        H = self.headers_for(user_id)

        # A new link starts with no note.
        self.assertEqual(self.client.get(f"/links/{file_id}", headers=H).get_json()["notes"], "")

        set_resp = self.client.post(
            f"/links/{file_id}/settings", headers=H,
            json={"notes": "  Send to accounting before Friday  "},
        )
        # Stored trimmed, echoed back, and present on both the detail and list.
        self.assertEqual(set_resp.get_json()["notes"], "Send to accounting before Friday")
        listed = self.client.get("/links", headers=H).get_json()[0]
        self.assertEqual(listed["notes"], "Send to accounting before Friday")

        clear_resp = self.client.post(
            f"/links/{file_id}/settings", headers=H, json={"notes": ""}
        )
        self.assertEqual(clear_resp.get_json()["notes"], "")

    def test_notes_length_is_bounded(self):
        user_id = str(uuid.uuid4())
        self.add_user(user_id)
        file_id, _ = self.add_link(user_id)
        resp = self.client.post(
            f"/links/{file_id}/settings",
            headers=self.headers_for(user_id),
            json={"notes": "x" * 501},
        )
        self.assertEqual(resp.status_code, 400)

    def test_settings_touch_only_the_provided_fields(self):
        # Setting notes alone must not disturb an existing download cap.
        user_id = str(uuid.uuid4())
        self.add_user(user_id)
        file_id, _ = self.add_link(user_id, max_downloads=5)
        H = self.headers_for(user_id)
        resp = self.client.post(f"/links/{file_id}/settings", headers=H, json={"notes": "hi"})
        body = resp.get_json()
        self.assertEqual(body["notes"], "hi")
        self.assertEqual(body["max_downloads"], 5)

    # --- upload-time max downloads ---

    def upload_one_chunk(self, user_id, filename, extra=None):
        data = {
            "file": (io.BytesIO(b"hello"), filename),
            "fileName": filename,
            "chunkIndex": "0",
            "chunkOffset": "0",
            "totalChunks": "1",
            "fileSize": "5",
            "chunkSize": "5",
            "expires": "3600",
            "overwrite": "0",
        }
        if extra:
            data.update(extra)
        return self.client.post(
            "/upload", headers=self.headers_for(user_id), data=data,
            content_type="multipart/form-data",
        )

    def test_upload_persists_the_max_downloads_field(self):
        user_id = str(uuid.uuid4())
        self.add_user(user_id)
        self.assertEqual(self.upload_one_chunk(user_id, "capped.txt", {"maxDownloads": "4"}).status_code, 200)
        row = self.client.get("/links", headers=self.headers_for(user_id)).get_json()[0]
        self.assertEqual(row["max_downloads"], 4)
        self.assertEqual(row["notes"], "")

    def test_upload_without_max_downloads_is_unlimited(self):
        user_id = str(uuid.uuid4())
        self.add_user(user_id)
        self.assertEqual(self.upload_one_chunk(user_id, "free.txt").status_code, 200)
        row = self.client.get("/links", headers=self.headers_for(user_id)).get_json()[0]
        self.assertIsNone(row["max_downloads"])

    def test_upload_rejects_invalid_max_downloads(self):
        user_id = str(uuid.uuid4())
        self.add_user(user_id)
        self.assertEqual(self.upload_one_chunk(user_id, "bad.txt", {"maxDownloads": "-3"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
