import sqlite3
import tempfile
import unittest

from pathlib import Path

from store_schema import configure_connection, initialize_schema


ROOT = Path(__file__).resolve().parents[1]


class CanonicalSchemaTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "store.db"
        self.conn = configure_connection(sqlite3.connect(self.db_path))
        initialize_schema(self.conn)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def test_connection_pragmas_and_schema_version_are_enforced(self):
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 3)
        state = self.conn.execute(
            "SELECT dflt_value FROM pragma_table_info('upload_metadata') "
            "WHERE name = 'state'"
        ).fetchone()
        self.assertEqual(state, ("'receiving'",))

    def test_foreign_key_cascades_are_live(self):
        self.conn.execute(
            """
            INSERT INTO users
                (user_id, username, password_hash, role, quota_bytes, created)
            VALUES ('user-id', 'user', X'00', 'user', 1073741824, 'now')
            """
        )
        self.conn.execute(
            """
            INSERT INTO refresh_tokens
                (user_id, token, expires, created, allowed_ip)
            VALUES ('user-id', 'token', 1, 1, '127.0.0.1')
            """
        )
        self.conn.execute("DELETE FROM users WHERE user_id = 'user-id'")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM refresh_tokens").fetchone()[0],
            0,
        )

    def test_negative_sizes_are_rejected(self):
        self.conn.execute(
            """
            INSERT INTO users
                (user_id, username, password_hash, role, quota_bytes, created)
            VALUES ('user-id', 'user', X'00', 'user', 1073741824, 'now')
            """
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                """
                INSERT INTO links
                    (user_id, file_id, file_name, size)
                VALUES ('user-id', 'file-id', 'file.bin', -1)
                """
            )

    def test_server_and_cli_have_no_private_schema_definitions(self):
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        cli = (ROOT / "create_users.py").read_text(encoding="utf-8")
        self.assertNotIn("CREATE TABLE", server)
        self.assertNotIn("CREATE TABLE", cli)
        self.assertIn("initialize_schema(conn)", server)
        self.assertIn("initialize_schema(conn)", cli)
