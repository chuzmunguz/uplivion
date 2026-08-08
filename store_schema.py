"""Canonical, versioned SQLite schema shared by the server and operator CLI."""


SCHEMA_VERSION = 3


def configure_connection(conn):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("SQLite foreign-key enforcement could not be enabled")
    return conn


def initialize_schema(conn):
    current_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if current_version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema {current_version} is newer than supported "
            f"version {SCHEMA_VERSION}"
        )

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT UNIQUE NOT NULL,
            username TEXT UNIQUE NOT NULL,
            first_name TEXT NOT NULL DEFAULT '',
            last_name TEXT NOT NULL DEFAULT '',
            password_hash BLOB NOT NULL,
            role TEXT NOT NULL DEFAULT 'user'
                CHECK (role IN ('superadmin', 'admin', 'user')),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'disabled')),
            auth_version INTEGER NOT NULL DEFAULT 0,
            quota_bytes INTEGER NOT NULL,
            created TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_username_nocase
            ON users(username COLLATE NOCASE);

        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            token TEXT NOT NULL UNIQUE,
            expires INTEGER NOT NULL,
            created INTEGER NOT NULL,
            allowed_ip TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id
            ON refresh_tokens(user_id);

        CREATE TABLE IF NOT EXISTS quota_reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            file_id TEXT NOT NULL,
            size INTEGER NOT NULL CHECK (size >= 0),
            created_at INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_quota_reservations_user_id
            ON quota_reservations(user_id, file_id, created_at);

        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            file_id TEXT UNIQUE NOT NULL,
            file_name TEXT NOT NULL,
            size INTEGER NOT NULL CHECK (size >= 0),
            hash TEXT,
            uploaded TEXT,
            linktoken TEXT,
            expires INTEGER,
            created TEXT,
            revoked INTEGER,
            max_downloads INTEGER,
            download_count INTEGER NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_links_user_id ON links(user_id);

        CREATE TABLE IF NOT EXISTS upload_chunks (
            user_id TEXT NOT NULL,
            file_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            uploaded_at INTEGER NOT NULL,
            PRIMARY KEY (user_id, file_id, chunk_index),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_upload_chunks_user_file
            ON upload_chunks (user_id, file_id);

        CREATE TABLE IF NOT EXISTS upload_metadata (
            user_id TEXT NOT NULL,
            file_id TEXT NOT NULL,
            file_name TEXT NOT NULL,
            total_chunks INTEGER NOT NULL CHECK (total_chunks > 0),
            total_size INTEGER NOT NULL CHECK (total_size > 0),
            chunk_size INTEGER NOT NULL CHECK (chunk_size > 0),
            expires INTEGER NOT NULL,
            replace_file_id TEXT,
            state TEXT NOT NULL DEFAULT 'receiving'
                CHECK (state IN ('receiving', 'finalizing')),
            PRIMARY KEY (user_id, file_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_upload_metadata_user_file
            ON upload_metadata (user_id, file_name);
        """
    )

    duplicates = conn.execute(
        """
        SELECT user_id, file_name, COUNT(*)
        FROM links
        GROUP BY user_id, file_name
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    if duplicates:
        raise RuntimeError(
            "Duplicate per-user filenames block schema version 2; "
            "back up the database and resolve these rows first: "
            f"{duplicates}"
        )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_links_user_file_name
        ON links(user_id, file_name)
        """
    )
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
