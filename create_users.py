import os
import sqlite3
import getpass
import bcrypt
import uuid
from datetime import datetime

from request_validation import NAME_MAX, text_value, validate_password_policy
from store_schema import configure_connection, initialize_schema
from username_validation import validate_username

DEFAULT_DB_PATH = "/var/lib/uplivion/store.db"


def main(db_path=None):
    valid, username, username_error = validate_username(
        input("Enter username: ")
    )
    if not valid:
        print(username_error)
        return 1

    while True:
        password = getpass.getpass("Enter password: ")
        valid, reason = validate_password_policy(password)
        if not valid:
            print(reason)
            continue
        repeat_password = getpass.getpass("Repeat password: ")
        if password != repeat_password:
            print("Passwords do not match. Please try again.")
            continue
        break

    # The CLI is the only place a superadmin can be minted; the web panel offers
    # just admin/user (see server.admin_create_user / admin_set_role).
    role = input("Enter role (superadmin/admin/user) [user]: ").strip().lower() or "user"
    if role not in ("superadmin", "admin", "user"):
        print("Role must be 'superadmin', 'admin', or 'user'.")
        return 1

    ok, name_error, first_name = text_value(
        input("Enter first name (optional): "), "First name", NAME_MAX
    )
    if not ok:
        print(name_error)
        return 1
    ok, name_error, last_name = text_value(
        input("Enter last name (optional): "), "Last name", NAME_MAX
    )
    if not ok:
        print(name_error)
        return 1

    unit_to_bytes = {"MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}
    while True:
        try:
            amount = float(input("Enter quota amount (e.g. 10): "))
        except ValueError:
            print("Invalid number. Please enter a number.")
            continue
        if amount <= 0:
            print("Quota must be greater than 0.")
            continue
        unit = (input("Enter unit (MB/GB/TB) [GB]: ").strip().upper() or "GB")
        if unit not in unit_to_bytes:
            print("Unit must be MB, GB, or TB.")
            continue
        quota_bytes = int(round(amount * unit_to_bytes[unit]))
        break

    resolved_db_path = db_path or os.environ.get(
        "UPLIVION_DB_PATH", DEFAULT_DB_PATH
    )
    try:
        conn = configure_connection(
            sqlite3.connect(resolved_db_path, timeout=10)
        )
    except sqlite3.OperationalError as exc:
        print(f"Cannot open {resolved_db_path}: {exc}")
        print("Try: sudo -u uplivion ./venv/bin/python3 create_users.py")
        return 1
    try:
        initialize_schema(conn)
        c = conn.cursor()
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
        created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute(
            "SELECT user_id FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        )
        existing = c.fetchone()

        if existing:
            overwrite = input(
                f"User '{username}' already exists. Replace it? (y/n): "
            ).strip().lower()
            if overwrite != "y":
                print("No changes made.")
                return 0

            user_id = existing[0]
            c.execute(
                """
                UPDATE users
                SET password_hash = ?, quota_bytes = ?, created = ?,
                    first_name = ?, last_name = ?,
                    auth_version = auth_version + 1
                WHERE user_id = ?
                """,
                (password_hash, quota_bytes, created,
                 first_name, last_name, user_id),
            )
            if c.rowcount != 1:
                conn.rollback()
                print("User replacement failed; no changes committed.")
                return 1
            c.execute(
                "DELETE FROM refresh_tokens WHERE user_id = ?", (user_id,)
            )
            conn.commit()
            print(f"User '{username}' replaced successfully.")
            return 0

        user_uuid = str(uuid.uuid4())
        c.execute(
            """
            INSERT INTO users
                (user_id, username, first_name, last_name,
                 password_hash, role, quota_bytes, created)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_uuid,
                username,
                first_name,
                last_name,
                password_hash,
                role,
                quota_bytes,
                created,
            ),
        )
        conn.commit()
        print(
            f"User '{username}' (role={role}) with quota {amount:g} {unit} created successfully."
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
