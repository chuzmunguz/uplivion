import importlib
import io
import os
import sys
import tempfile
import time
import unittest

from pathlib import Path
from unittest import mock

import jwt


class CrashSafeStorageTest(unittest.TestCase):
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
        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO users
                    (user_id, username, password_hash, role, quota_bytes, created)
                VALUES ('user-id', 'user', X'00', 'user', 1073741824, 'now')
                """
            )
            conn.commit()
        token = jwt.encode(
            {"user_id": "user-id", "username": "user", "ip": "127.0.0.1", "aver": 0},
            os.environ["ACCESS_TOKEN_SECRET"],
            algorithm="HS256",
        )
        self.headers = {"Authorization": f"Bearer {token}"}

    def tearDown(self):
        sys.modules.pop("server", None)
        self.temp_dir.cleanup()

    def add_link(self, file_id, name, content, recorded_size=None):
        path = self.server.resolve_upload_path(file_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO links
                    (user_id, file_id, file_name, size, hash, uploaded,
                     linktoken, expires, created, revoked)
                VALUES ('user-id', ?, ?, ?, '', '', ?, ?, '', 0)
                """,
                (
                    file_id,
                    name,
                    len(content) if recorded_size is None else recorded_size,
                    f"token-{file_id}",
                    int(time.time()) + 3600,
                ),
            )
            conn.commit()

    def overwrite(self, name, content=b"new", upload_id=None):
        data = {
            "file": (io.BytesIO(content), name),
            "fileName": name,
            "chunkIndex": "0",
            "chunkOffset": "0",
            "totalChunks": "1",
            "fileSize": str(len(content)),
            "chunkSize": str(len(content)),

            "expires": "3600",
            "overwrite": "1",
        }
        if upload_id is not None:
            data["uploadID"] = upload_id
        return self.client.post(
            "/upload",
            headers=self.headers,
            data=data,
            content_type="multipart/form-data",
        )

    def test_quota_rejection_preserves_old_row_and_bytes(self):
        self.add_link("user-id_old", "old.bin", b"old bytes")
        self.add_link(
            "user-id_filler",
            "filler.bin",
            b"x",
            recorded_size=1024 * 1024 * 1024,
        )

        response = self.overwrite("old.bin", b"replacement")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(
            self.server.resolve_upload_path("user-id_old").read_bytes(),
            b"old bytes",
        )
        with self.server.db_session() as conn:
            row = conn.execute(
                "SELECT file_id FROM links WHERE file_name = 'old.bin'"
            ).fetchone()
        self.assertEqual(row, ("user-id_old",))

    def test_failed_commit_preserves_old_row_and_bytes(self):
        self.add_link("user-id_old", "old.bin", b"old bytes")
        with self.server.db_session() as conn:
            conn.execute(
                """
                CREATE TRIGGER fail_replacement
                BEFORE INSERT ON links
                WHEN NEW.file_name = 'old.bin'
                BEGIN
                    SELECT RAISE(ABORT, 'forced insert failure');
                END
                """
            )
            conn.commit()

        response = self.overwrite("old.bin")
        self.assertEqual(response.status_code, 500)
        retry_id = response.get_json()["fileID"]
        self.assertEqual(
            self.server.resolve_upload_path("user-id_old").read_bytes(),
            b"old bytes",
        )
        with self.server.db_session() as conn:
            row = conn.execute(
                "SELECT file_id FROM links WHERE file_name = 'old.bin'"
            ).fetchone()
            state = conn.execute(
                "SELECT state FROM upload_metadata WHERE file_id = ?",
                (retry_id,),
            ).fetchone()
        self.assertEqual(row, ("user-id_old",))
        self.assertEqual(state, ("finalizing",))

        progress = self.client.get(
            "/progress",
            headers={**self.headers, "X-Upload-ID": retry_id},
        )
        self.assertEqual(progress.status_code, 200)
        self.assertEqual(progress.get_json()["uploadedChunks"], [])

        with self.server.db_session() as conn:
            conn.execute("DROP TRIGGER fail_replacement")

        retry = self.overwrite("old.bin", upload_id=retry_id)
        self.assertEqual(retry.status_code, 200, retry.get_data(as_text=True))
        with self.server.db_session() as conn:
            replacement = conn.execute(
                "SELECT file_id FROM links WHERE file_name = 'old.bin'"
            ).fetchone()
            metadata = conn.execute(
                "SELECT COUNT(*) FROM upload_metadata WHERE file_id = ?",
                (retry_id,),
            ).fetchone()[0]
            reservation = conn.execute(
                "SELECT COUNT(*) FROM quota_reservations WHERE file_id = ?",
                (retry_id,),
            ).fetchone()[0]
        self.assertNotEqual(replacement, ("user-id_old",))
        self.assertEqual(
            self.server.resolve_upload_path(replacement[0]).read_bytes(),
            b"new",
        )
        self.assertFalse(
            self.server.resolve_upload_path("user-id_old").exists()
        )
        self.assertEqual(metadata, 0)
        self.assertEqual(reservation, 0)

    def test_reconciliation_ages_partials_and_fresh_orphans(self):
        stale_id = "user-id/00000000-0000-4000-8000-000000000000.part"
        fresh_id = "user-id/fresh-orphan"
        finalizing_id = "user-id/00000000-0000-4000-8000-000000000001.part"
        stale_path = self.server.resolve_upload_path(stale_id)
        fresh_path = self.server.resolve_upload_path(fresh_id)
        finalizing_path = self.server.resolve_upload_path(
            finalizing_id.removesuffix(".part")
        )
        stale_path.parent.mkdir(parents=True, exist_ok=True)
        stale_path.write_bytes(b"stale")
        fresh_path.write_bytes(b"fresh")
        finalizing_path.write_bytes(b"ready")
        old = time.time() - 25 * 3600
        os.utime(stale_path, (old, old))
        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO upload_metadata
                    (user_id, file_id, file_name, total_chunks, total_size,
                     chunk_size, expires, replace_file_id)
                VALUES ('user-id', ?, 'stale.bin', 2, 2, 1, 3600, NULL)
                """,
                (stale_id,),
            )
            conn.execute(
                """
                INSERT INTO quota_reservations
                    (user_id, file_id, size, created_at)
                VALUES ('user-id', ?, 2, ?)
                """,
                (stale_id, int(old)),
            )
            conn.execute(
                """
                INSERT INTO upload_metadata
                    (user_id, file_id, file_name, total_chunks, total_size,
                     chunk_size, expires, replace_file_id, state)
                VALUES ('user-id', ?, 'ready.bin', 1, 5, 5, 3600, NULL,
                        'finalizing')
                """,
                (finalizing_id,),
            )
            conn.commit()

        self.server.cleanup_db_and_disk("127.0.0.1", "user-id", "user")
        self.assertFalse(stale_path.exists())
        self.assertTrue(fresh_path.exists())
        self.assertTrue(finalizing_path.exists())
        with self.server.db_session() as conn:
            state = conn.execute(
                "SELECT state FROM upload_metadata WHERE file_id = ?",
                (finalizing_id,),
            ).fetchone()
        self.assertEqual(state, ("finalizing",))

    def test_cancel_removes_a_staged_final_object(self):
        upload_id = "user-id/00000000-0000-4000-8000-000000000002.part"
        final_path = self.server.resolve_upload_path(
            upload_id.removesuffix(".part")
        )
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_bytes(b"ready")
        with self.server.db_session() as conn:
            conn.execute(
                """
                INSERT INTO upload_metadata
                    (user_id, file_id, file_name, total_chunks, total_size,
                     chunk_size, expires, replace_file_id, state)
                VALUES ('user-id', ?, 'ready.bin', 1, 5, 5, 3600, NULL,
                        'finalizing')
                """,
                (upload_id,),
            )
            conn.execute(
                """
                INSERT INTO quota_reservations
                    (user_id, file_id, size, created_at)
                VALUES ('user-id', ?, 5, ?)
                """,
                (upload_id, int(time.time())),
            )

        response = self.client.post(
            "/cancel",
            headers=self.headers,
            json={"uploadID": upload_id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(final_path.exists())
        with self.server.db_session() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM upload_metadata WHERE file_id = ?",
                    (upload_id,),
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM quota_reservations WHERE file_id = ?",
                    (upload_id,),
                ).fetchone()[0],
                0,
            )

    def test_hash_failure_returns_the_durable_resume_id(self):
        with mock.patch.object(
            Path,
            "open",
            side_effect=OSError("forced hash read failure"),
        ):
            failed = self.overwrite("new.bin")

        self.assertEqual(failed.status_code, 500)
        upload_id = failed.get_json()["fileID"]
        with self.server.db_session() as conn:
            state = conn.execute(
                "SELECT state FROM upload_metadata WHERE file_id = ?",
                (upload_id,),
            ).fetchone()
        self.assertEqual(state, ("finalizing",))

        retry = self.overwrite("new.bin", upload_id=upload_id)
        self.assertEqual(retry.status_code, 200, retry.get_data(as_text=True))
        with self.server.db_session() as conn:
            row = conn.execute(
                "SELECT file_id FROM links WHERE file_name = 'new.bin'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(
            self.server.resolve_upload_path(row[0]).read_bytes(),
            b"new",
        )
