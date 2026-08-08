#!/usr/bin/env bash
set -euo pipefail
# Creates test accounts after install.sh --wipe-data.
# Accounts only — no files, no links.
#
# Usage:
#   sudo ./seed.sh                         # re-execs as uplivion automatically
#   sudo -u uplivion ./seed.sh                 # also fine
#   UPLIVION_DB_PATH=/path/to/db ./seed.sh     # override (any user with DB access)
#   UPLIVION_APP_DIR=/path/to/app ./seed.sh    # override the installed app dir
#
# The interpreter and Python modules come from the installed app (its venv),
# not this checkout — seed.sh is run from the checkout but is not deployed.

APP_DIR="${UPLIVION_APP_DIR:-/var/www/uplivion}"

if [ "$(id -u)" -eq 0 ]; then
    exec sudo -u uplivion "$0" "$@"
fi

"${APP_DIR}/venv/bin/python" - "$APP_DIR" <<'PYEOF'
import os
import sys
import sqlite3
import uuid
from datetime import datetime

sys.path.insert(0, sys.argv[1])
import bcrypt
from store_schema import configure_connection, initialize_schema

DB_PATH = os.environ.get("UPLIVION_DB_PATH", "/var/lib/uplivion/store.db")

# (username, password, quota_gb, role, first_name, last_name)
# A few accounts are deliberately left nameless to exercise the admin panel's
# empty-name rendering.
ACCOUNTS = [
    ("super", "Abcd1234!", 50, "superadmin", "Alonzo", "Church"),
    ("super2", "Abcd1234!", 50, "superadmin", "", ""),
    ("admin", "Abcd1234!", 50, "admin", "Ada", "Lovelace"),
    ("user1", "Abcd1234!", 10, "user", "Grace", "Hopper"),
    ("user2", "Abcd1234!", 10, "user", "Alan", "Turing"),
    ("user3", "Abcd1234!", 10, "user", "Katherine", "Johnson"),
    ("user4", "Abcd1234!", 10, "user", "Dennis", "Ritchie"),
    ("user5", "Abcd1234!", 10, "user", "Margaret", "Hamilton"),
    ("user6", "Abcd1234!", 10, "user", "Linus", "Torvalds"),
    ("user7", "Abcd1234!", 10, "user", "Barbara", "Liskov"),
    ("user8", "Abcd1234!", 10, "user", "", ""),
    ("user9", "Abcd1234!", 10, "user", "Donald", "Knuth"),
    ("user10", "Abcd1234!", 10, "user", "Radia", "Perlman"),
    ("user11", "Abcd1234!", 10, "user", "Ken", "Thompson"),
    ("user12", "Abcd1234!", 10, "user", "", ""),
    ("user13", "Abcd1234!", 10, "user", "Edsger", "Dijkstra"),
    ("user14", "Abcd1234!", 10, "user", "Frances", "Allen"),
    ("user15", "Abcd1234!", 10, "user", "Tim", "Berners-Lee"),
    ("user16", "Abcd1234!", 10, "user", "Shafi", "Goldwasser"),
]

try:
    conn = configure_connection(sqlite3.connect(DB_PATH, timeout=10))
except Exception as exc:
    print(f"Cannot open {DB_PATH}: {exc}")
    print("Try: sudo ./seed.sh")
    sys.exit(1)
try:
    initialize_schema(conn)
    created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for username, password, quota_gb, role, first_name, last_name in ACCOUNTS:
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
        quota_bytes = quota_gb * 1024 ** 3
        conn.execute(
            """
            INSERT INTO users
                (user_id, username, first_name, last_name,
                 password_hash, role, quota_bytes, created)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), username, first_name, last_name,
             password_hash, role, quota_bytes, created),
        )
        print(f"  {username:10s}  role={role:5s}  quota={quota_gb}GB")
    conn.commit()
    print(f"Seeded {len(ACCOUNTS)} accounts into {DB_PATH}")
finally:
    conn.close()
PYEOF
