# --- Libraries import ---
import sqlite3, secrets, hashlib, time, jwt, bcrypt, uuid, os, re, ipaddress, hmac, base64, threading, logging, unicodedata, mimetypes, shutil
from contextlib import contextmanager
from flask import Flask, g, request, jsonify, make_response, abort
from datetime import datetime, timedelta, timezone
from pathlib import Path
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from request_validation import (
    NAME_MAX,
    PASSWORD_MAX_BYTES,
    text_value,
    validate_password_policy,
)
from store_schema import configure_connection, initialize_schema
from username_validation import validate_username

app = Flask(__name__)
# Gunicorn is bound to loopback and receives exactly one trusted forwarding hop
# from Nginx. Never expose this WSGI service directly to untrusted clients.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024

# --- Configuration ---
UPLOAD_DIR = os.environ.get("UPLIVION_UPLOAD_DIR", "/var/lib/uplivion/share")
DB_PATH = os.environ.get("UPLIVION_DB_PATH", "/var/lib/uplivion/store.db")
SECRET_KEY = os.environ["SECRET_KEY"].encode("utf-8")  # the key must be converted from string to bytes to be passed to hmac.new()
BASE_URL = os.environ.get("UPLIVION_PUBLIC_ORIGIN", "https://localhost:8000")


# Access to backend restrictions (except for downloading links)
ALLOWED_IP_RANGES = [
    item.strip()
    for item in os.environ.get(
        "UPLIVION_ALLOWED_IP_RANGES",
        "192.168.1.0/24,10.0.0.0/24,127.0.0.1",
    ).split(",")
    if item.strip()
]

# Token generation
ACCESS_TOKEN_SECRET = os.environ["ACCESS_TOKEN_SECRET"]
ACCESS_TOKEN_DURATION = 15 * 60                 # Access token expiry (15m)
REFRESH_TOKEN_DURATION = 4 * 60 * 60            # Refresh token expiry (4h)
# Fixed valid bcrypt hash used only to equalize unknown-user login work.
DUMMY_PASSWORD_HASH = b"$2b$12$4Ucyo3KcYX.O30G6XpggveaDaaXiiBdmFMwFIXzdFhyz7csu3KcgS"

# Endpoints rate limit
PRIVATE_BUCKET = {}                             # {ip: {"requests": int, "last_refill": timestamp}}
AUTH_BUCKET = {}
PUBLIC_BUCKET = {}
rate_limit_lock = threading.Lock()
PRIVATE_REFILL_INTERVAL = 0.05  # 20req/sec     # seconds per request, used in every endpoint that requires full autentication
AUTH_REFILL_INTERVAL = 5        # 12 req/min    # used in semi-autenticated endpoints: /check and /session
PUBLIC_REFILL_INTERVAL = 10     # 6 req/min     # used in public endpoints: /login and /share
BURST = 14                                      # extra requests allowed (can be adjusted in each rate_limit() call)
MAX_CHUNK_SIZE = 5 * 1024 * 1024
MAX_LINK_EXPIRY = 99999 * 24 * 60 * 60
NOTES_MAX = 500                                 # per-file note length cap

# --- Initialize logger ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # show INFO and above

# Custom formatter
class CustomFormatter(logging.Formatter):
    def format(self, record):
        # Current time with timezone offset (local time + offset like +0100)
        now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        message = record.getMessage()
        pid = record.process  # worker PID (same as os.getpid())
        return f"[{now}] [{pid}] [{record.levelname}] {message}"


# A module reload must not attach another copy of the same handler.
if not any(getattr(handler, "_uplivion_console_handler", False) for handler in logger.handlers):
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(CustomFormatter())
    ch._uplivion_console_handler = True
    logger.addHandler(ch)
logger.propagate = False

# --- Ensure upload folder exists ---
os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- Database setup ---
def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    return configure_connection(conn)


def _get_request_conn():
    try:
        conn = g._uplivion_conn
    except AttributeError:
        conn = db_connect()
        g._uplivion_conn = conn
    return conn


@contextmanager
def db_session():
    """Yield the per-request connection with transaction semantics."""
    try:
        conn = _get_request_conn()
        reused = True
    except RuntimeError:
        conn = db_connect()
        reused = False
    try:
        with conn:
            yield conn
    finally:
        if not reused:
            conn.close()


@app.teardown_appcontext
def _close_request_conn(exc):
    conn = g.pop("_uplivion_conn", None)
    if conn is not None:
        conn.close()


db_write_lock = threading.Lock()

with db_write_lock, db_session() as conn:
    initialize_schema(conn)



# --- Helpers ---

# Resolve storage identifiers inside the upload root. Identifiers can come from
# durable rows created by older versions, so containment is enforced without
# changing their on-disk format.
def resolve_upload_path(file_id):
    if not isinstance(file_id, str) or not file_id:
        raise ValueError("Invalid file identifier")

    upload_root = Path(UPLOAD_DIR).resolve()
    candidate = (upload_root / file_id).resolve()
    try:
        candidate.relative_to(upload_root)
    except ValueError as exc:
        raise ValueError("File identifier resolves outside the upload directory") from exc
    return candidate


def valid_owned_upload_id(user_id, file_id):
    prefix = f"{user_id}/"
    if not isinstance(file_id, str) or not file_id.startswith(prefix) or not file_id.endswith(".part"):
        return False
    try:
        uuid.UUID(file_id[len(prefix):-len(".part")])
    except (TypeError, ValueError):
        return False
    return True



# Sanitize filenames
# Remove characters that are unsafe for file system / URLs. Keep alphanumerics, dot, dash, underscore, space and parentheses)
def sanitize_filename(filename: str) -> str:
    safe_name = unicodedata.normalize("NFC", filename)              # Normalize Unicode
    safe_name = re.sub(r'[^A-Za-z0-9\s._\-\(\)]', '_', safe_name)   # Replace unsafe characters with underscore
    safe_name = re.sub(r'[\s_]+', '_', safe_name)                   # Collapse multiple consecutive underscores or spaces into a single underscore
    safe_name = safe_name.strip()                                   # Strip leading/trailing whitespace
    safe_name = os.path.basename(safe_name)                         # Ensure no directory traversal
    return safe_name[:255]                                          # Limit length


def internal_error_response(
    context, client_ip, username, error, retry_upload_id=None
):
    """Log private exception detail and return a correlation-safe response."""
    request_id = secrets.token_hex(8)
    logger.exception(
        "[%s] [%s] [%s] %s: %s",
        client_ip,
        username,
        request_id,
        context,
        error,
    )
    payload = {"error": "Internal server error", "request_id": request_id}
    if retry_upload_id is not None:
        payload["fileID"] = retry_upload_id
    return jsonify(payload), 500



# Remove DB entries when the file no longer exists on disk, and vice-versa
def cleanup_db_and_disk(client_ip, user_id, username):
    upload_dir_abs = os.path.abspath(UPLOAD_DIR)

    try:
        with db_write_lock, db_session() as conn:
            c = conn.cursor()

            # Get all filenames from DB
            c.execute("SELECT file_id FROM links WHERE user_id = ?", (user_id,))
            db_files = {row[0] for row in c.fetchall()}
            c.execute(
                """
                SELECT file_id FROM upload_metadata
                WHERE user_id = ? AND state = 'finalizing'
                """,
                (user_id,),
            )
            finalizing_files = {
                row[0].removesuffix(".part"): row[0] for row in c.fetchall()
            }

            user_dir = os.path.join(upload_dir_abs, user_id)
            try:
                disk_files = {f"{user_id}/{f}" for f in os.listdir(user_dir)}
            except FileNotFoundError:
                disk_files = set()
            except OSError as e:
                logger.error("[%s] [%s] Failed to list %s: %s", client_ip, username, user_dir, e)
                return

            # Remove DB entries for missing files
            missing_files = db_files - disk_files
            for file_id in missing_files:
                c.execute("DELETE FROM links WHERE file_id = ?", (file_id,))
                logger.info("[%s] [%s] Removed DB entry for missing file: %s", client_ip, username, file_id)

            # Remove orphan files on disk
            orphan_files = disk_files - db_files
            for file_id in orphan_files:
                try:
                    filepath = resolve_upload_path(file_id)
                except ValueError:
                    logger.warning("[%s] [%s] Skipping suspicious file outside upload dir: %s", client_ip, username, file_id)
                    continue

                finalizing_upload_id = finalizing_files.get(file_id)
                if finalizing_upload_id:
                    try:
                        if filepath.stat().st_mtime >= time.time() - 24 * 3600:
                            continue
                        filepath.unlink()
                        c.execute(
                            "DELETE FROM upload_chunks WHERE user_id = ? AND file_id = ?",
                            (user_id, finalizing_upload_id),
                        )
                        c.execute(
                            "DELETE FROM upload_metadata WHERE user_id = ? AND file_id = ?",
                            (user_id, finalizing_upload_id),
                        )
                        c.execute(
                            "DELETE FROM quota_reservations WHERE user_id = ? AND file_id = ?",
                            (user_id, finalizing_upload_id),
                        )
                    except OSError as e:
                        logger.error(
                            "[%s] [%s] Failed to remove stale finalizing upload %s: %s",
                            client_ip,
                            username,
                            file_id,
                            e,
                        )
                    continue

                # Fresh files can be between final rename and the DB commit. Partial
                # uploads are resumable for 24 hours; older state is abandoned.
                if file_id.endswith(".part"):
                    try:
                        if filepath.stat().st_mtime >= time.time() - 24 * 3600:
                            continue
                        filepath.unlink()
                        c.execute(
                            "DELETE FROM upload_chunks WHERE user_id = ? AND file_id = ?",
                            (user_id, file_id),
                        )
                        c.execute(
                            "DELETE FROM upload_metadata WHERE user_id = ? AND file_id = ?",
                            (user_id, file_id),
                        )
                        c.execute(
                            "DELETE FROM quota_reservations WHERE user_id = ? AND file_id = ?",
                            (user_id, file_id),
                        )
                    except OSError as e:
                        logger.error("[%s] [%s] Failed to remove stale partial %s: %s", client_ip, username, file_id, e)
                    continue

                try:
                    if filepath.stat().st_mtime >= time.time() - 24 * 3600:
                        continue
                except OSError as e:
                    logger.error("[%s] [%s] Failed to inspect orphan %s: %s", client_ip, username, file_id, e)
                    continue

                try:
                    filepath.unlink()
                    logger.info("[%s] [%s] Deleted orphan file: %s", client_ip, username, file_id)
                except OSError as e:
                    logger.error("[%s] [%s] Failed to delete %s: %s", client_ip, username, file_id, e)

            conn.commit()

    except sqlite3.Error as e:
        logger.error("[%s] [%s] Database error during cleanup: %s", client_ip, username, e)
    except Exception as e:
        logger.error("[%s] [%s] Unexpected error during cleanup: %s", client_ip, username, e)



# ProxyFix extracts the client IP from the last X-Forwarded-For value
# (the one Nginx appended) before request handling.
def get_client_ip():
    try:
        return str(ipaddress.ip_address(request.remote_addr))
    except ValueError:
        abort(403)



# Check if the client IP is in one of the allowed ranges
def is_ip_allowed(client_ip):
    # Special case: allow all
    if "0.0.0.0" in ALLOWED_IP_RANGES:
        return

    # Allow ips on the list
    ip = ipaddress.ip_address(client_ip)
    for net in ALLOWED_IP_RANGES:
        if ip in ipaddress.ip_network(net, strict=False):
            return

    # Abort execution if not allowed
    abort(403)



# Rate limit function
def rate_limit(ip, now, endpoint_bucket, refill_interval, burst=BURST):
    effective_max = 1 + burst

    with rate_limit_lock:
        bucket = endpoint_bucket.get(ip, {"requests": effective_max, "last_refill": now})
        requests = bucket["requests"]
        last_refill = bucket["last_refill"]
        elapsed = now - last_refill
        regenerated = int(elapsed // refill_interval)
        requests = min(effective_max, requests + regenerated)
        last_refill += regenerated * refill_interval
        if requests == effective_max:
            last_refill = now
        if requests <= 0:
            endpoint_bucket[ip] = {"requests": requests, "last_refill": last_refill}
            abort(429)
        requests -= 1
        endpoint_bucket[ip] = {"requests": requests, "last_refill": last_refill}



# Validate JWT access token from Authorization header
def check_access_token():
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        abort(401)

    token = auth.split(" ")[1]
    client_ip = get_client_ip()

    try:
        payload = jwt.decode(token, ACCESS_TOKEN_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        abort(401)
    except jwt.InvalidTokenError:
        abort(401)

    # IP check
    if payload.get("ip") and payload.get("ip") != client_ip:
        abort(401)

    with db_session() as conn:
        row = conn.execute(
            "SELECT auth_version, role, status, first_name, last_name "
            "FROM users WHERE user_id = ?",
            (payload["user_id"],),
        ).fetchone()
        if not row or row[0] != payload["aver"]:
            abort(401)
        if row[2] != "active":
            abort(401)
        payload["_role"] = row[1]
        payload["_first_name"] = row[3]
        payload["_last_name"] = row[4]

    return payload


# Roles form a hierarchy: superadmin > admin > user. Both privileged roles reach
# the admin endpoints; the target guard below then narrows what each may touch.
ADMIN_ROLES = ("superadmin", "admin")


def require_admin(payload):
    if payload.get("_role") not in ADMIN_ROLES:
        abort(403)


def _is_super(payload):
    return payload.get("_role") == "superadmin"


def _guard_admin_target(payload, target_role):
    """Return a JSON 403 tuple if the caller may not manage a target of this
    role, else None. Superadmins manage everyone (users, admins, and other
    superadmins); regular admins manage only regular users."""
    if _is_super(payload):
        return None
    if target_role == "user":
        return None
    return jsonify({"error": "Admins can only manage regular users"}), 403


def parse_optional_names(data):
    """Validate the optional first/last name fields of a request body.

    Returns (ok, error, first_name, last_name). Missing fields become "".
    """
    ok, error, first = text_value(data.get("first_name"), "First name", NAME_MAX)
    if not ok:
        return False, error, None, None
    ok, error, last = text_value(data.get("last_name"), "Last name", NAME_MAX)
    if not ok:
        return False, error, None, None
    # text_value yields None for an absent field; the name columns are NOT NULL.
    return True, None, first or "", last or ""


def _collect_user_file_ids(conn, user_id):
    return [
        r[0]
        for r in conn.execute(
            "SELECT file_id FROM links WHERE user_id = ?", (user_id,)
        ).fetchall()
    ]


def _purge_user_file_rows(conn, user_id):
    """Delete a user's file + in-progress upload rows, keeping the account.

    Caller holds db_write_lock and commits. (Account deletion instead relies on
    ON DELETE CASCADE, so it does not call this.)
    """
    conn.execute("DELETE FROM links WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM quota_reservations WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM upload_metadata WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM upload_chunks WHERE user_id = ?", (user_id,))


def _remove_user_files_from_disk(user_id, file_ids):
    """Unlink a user's finished files and clear their per-user directory.

    A later upload recreates the directory via filepath.parent.mkdir(...).
    """
    for file_id in file_ids:
        try:
            resolve_upload_path(file_id).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
    user_dir = Path(UPLOAD_DIR) / user_id
    if user_dir.is_dir():
        shutil.rmtree(user_dir, ignore_errors=True)


def _is_last_active_superadmin(conn, user_id):
    """True if user_id is an active superadmin and the only one left.

    Superadmin is the CLI-rooted top tier; the system must always keep at least
    one active, so the web paths that could remove one (disable, delete, and
    self-delete) refuse when it would drop the count to zero.
    """
    row = conn.execute(
        "SELECT role, status FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row or row[0] != "superadmin" or row[1] != "active":
        return False
    active_supers = conn.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'superadmin' AND status = 'active'"
    ).fetchone()[0]
    return active_supers <= 1



def _link_row_to_dict(row):
    """Shape one links row into the owner-facing JSON.

    Column order (shared by the list and per-file endpoints):
    file_id, file_name, size, linktoken, expires, created, revoked,
    download_count, max_downloads.
    """
    file_id, file_name, size, linktoken, expires, created, revoked, download_count, max_downloads, notes = row
    return {
        "file_id": file_id,
        "filename": file_name,
        "size": size,
        "link": f"{BASE_URL}/share/{linktoken}",
        "created": created,
        "expires_in": expires - int(time.time()),
        "revoked": bool(revoked),
        "download_count": download_count,
        "max_downloads": max_downloads,
        "notes": notes or "",
    }


# The columns _link_row_to_dict expects, in order, for reuse across endpoints.
LINK_DICT_COLUMNS = (
    "file_id, file_name, size, linktoken, expires, created, revoked, "
    "download_count, max_downloads, notes"
)


@app.route("/share/<link_token>", methods=["GET"])
def share_file(link_token):
    if not link_token:
        abort(404)

    # Endpoint protections
    client_ip = get_client_ip()
    now_ts = int(time.time())
    rate_limit(client_ip, now_ts, PUBLIC_BUCKET, PUBLIC_REFILL_INTERVAL)

    now = int(time.time())

    # Look up, enforce the download limit, and count the hand-off in one atomic
    # transaction. BEGIN IMMEDIATE serializes this across worker processes so a
    # burst of parallel requests cannot slip past max_downloads.
    with db_write_lock, db_session() as conn:
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        c.execute(
            "SELECT file_id, file_name, revoked, expires, download_count, max_downloads "
            "FROM links WHERE linktoken = ?",
            (link_token,),
        )
        row = c.fetchone()
        if not row:
            conn.rollback()
            abort(404)

        file_id, file_name, revoked, db_expires, download_count, max_downloads = row

        # Revoked or expired links are gone.
        if revoked == 1 or now > db_expires:
            conn.rollback()
            abort(404)

        # Recalculate the expected link token (constant-time compare).
        message = f"{file_id}:{db_expires}".encode()
        expected_token = base64.urlsafe_b64encode(hmac.new(SECRET_KEY, message, hashlib.sha256).digest()).decode().rstrip("=")
        if not hmac.compare_digest(expected_token, link_token):
            conn.rollback()
            abort(404)

        # Download-count limit: an exhausted link is treated as gone.
        if max_downloads is not None and download_count >= max_downloads:
            conn.rollback()
            abort(404)

        # File must exist on disk before we count a hand-off.
        try:
            file_path = resolve_upload_path(file_id)
        except ValueError:
            conn.rollback()
            abort(404)
        if not os.path.isfile(file_path):
            conn.rollback()
            abort(404)

        # Count this successful hand-off to Nginx (read + increment are atomic).
        c.execute(
            "UPDATE links SET download_count = download_count + 1 WHERE file_id = ?",
            (file_id,),
        )
        conn.commit()

    # Respond with X-Accel-Redirect so Nginx serves the file
    mimetype = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    response = make_response()
    response.headers["Content-Type"] = mimetype
    response.headers["X-Accel-Redirect"] = f"/internal_share/{file_id}"
    response.headers["Content-Disposition"] = f'attachment; filename="{file_name}"'

    logger.info("[%s] Downloaded file %s (File ID: %s - mimetype: %s)", client_ip, file_name, file_id, mimetype)

    return response, 200



@app.route("/links", methods=["GET"])
def get_links():
    client_ip = get_client_ip()
    now_ts = int(time.time())

    # Endpoint protections
    is_ip_allowed(client_ip)
    rate_limit(client_ip, now_ts, PRIVATE_BUCKET, PRIVATE_REFILL_INTERVAL)
    payload = check_access_token()

    user_id = payload["user_id"]
    username = payload["username"]

    # Remove invalid database entries and orphan files on the disk to keep the links list clean
    cleanup_db_and_disk(client_ip, user_id, username)

    # Fetch links from database
    with db_session() as conn:
        c = conn.cursor()
        c.execute(
            f"SELECT {LINK_DICT_COLUMNS} FROM links WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        )
        rows = c.fetchall()

    # Build JSON response
    data = [_link_row_to_dict(r) for r in rows]

    return jsonify(data)



@app.route("/check", methods=["POST"])
def check():
    now_ts = int(time.time())
    client_ip = get_client_ip()

    # Endpoint protections
    is_ip_allowed(client_ip)
    rate_limit(client_ip, now_ts, AUTH_BUCKET, AUTH_REFILL_INTERVAL)
    payload = check_access_token()

    return jsonify({
        "status": "ok",
        "user_id": payload["user_id"],
        "username": payload["username"],
        "first_name": payload.get("_first_name", ""),
        "last_name": payload.get("_last_name", ""),
        "role": payload.get("_role", "user"),
    })



# --- /login ---
@app.route("/login", methods=["POST"])
def login():
    client_ip = get_client_ip()
    now_ts = int(time.time())

    # Endpoint protections
    is_ip_allowed(client_ip)
    rate_limit(client_ip, now_ts, PUBLIC_BUCKET, PUBLIC_REFILL_INTERVAL)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request"}), 400

    valid, username, _ = validate_username(data.get("username"))
    password = data.get("password")
    valid_password, _ = validate_password_policy(password)
    if not valid or not valid_password:
        return jsonify({"error": "Invalid username or password"}), 401

    with db_session() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT user_id, password_hash, status FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        )
        row = c.fetchone()

    password_hash = row[1] if row else DUMMY_PASSWORD_HASH
    password_matches = bcrypt.checkpw(password.encode(), password_hash)
    if not row or not password_matches:
        logger.warning("[%s] [%s] Login failed", client_ip, username)
        return jsonify({"error": "Invalid username or password"}), 401

    user_id = row[0]
    if row[2] != "active":
        logger.warning("[%s] [%s] Disabled account login attempt", client_ip, username)
        return jsonify({"error": "Account disabled"}), 403
    # --- Generate refresh token ---
    refresh_token = secrets.token_urlsafe(32)
    refresh_token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
    expires = int(time.time()) + REFRESH_TOKEN_DURATION

    with db_write_lock, db_session() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO refresh_tokens (token, user_id, expires, created, allowed_ip) VALUES (?, ?, ?, ?, ?)",
            (refresh_token_hash, user_id, expires, int(time.time()), client_ip)
        )

    # --- Send refresh token in secure cookie ---
    response = make_response(jsonify({"success": True}))
    response.set_cookie(
        "refresh_token",
        refresh_token,
        max_age=REFRESH_TOKEN_DURATION,
        path="/session",
        httponly=True,
        secure=True,
        samesite="Strict"
    )

    logger.info("[%s] [%s] User logged in", client_ip, username)

    return response



@app.route("/session", methods=["POST"])
def session_refresh():
    client_ip = get_client_ip()
    now_ts = int(time.time())

    # Endpoint protections
    is_ip_allowed(client_ip)
    rate_limit(client_ip, now_ts, AUTH_BUCKET, AUTH_REFILL_INTERVAL)

    # Cleanup expired tokens
    with db_write_lock, db_session() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM refresh_tokens WHERE expires < ?", (now_ts,))
        conn.commit()

    # Get refresh token from cookie and verify expiry
    cookie = request.cookies.get("refresh_token")
    if not cookie:
        return jsonify({"error": "Unauthorized"}), 401

    token_hash = hashlib.sha256(cookie.encode()).hexdigest()

    new_refresh_token = secrets.token_urlsafe(32)
    new_refresh_hash = hashlib.sha256(new_refresh_token.encode()).hexdigest()
    new_expires = now_ts + REFRESH_TOKEN_DURATION

    # Consume and replace in one write transaction. A concurrent replay can
    # observe rowcount 0 only after the winning transaction commits.
    with db_write_lock, db_session() as conn:
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        c.execute(
            """
            SELECT refresh_tokens.user_id, refresh_tokens.allowed_ip,
                   refresh_tokens.expires, users.username,
                   users.auth_version
            FROM refresh_tokens
            JOIN users ON users.user_id = refresh_tokens.user_id
            WHERE refresh_tokens.token = ?
            """,
            (token_hash,),
        )
        row = c.fetchone()
        if not row or now_ts > row[2]:
            conn.rollback()
            return jsonify({"error": "Unauthorized"}), 401

        user_id, allowed_ip, expires, username, auth_version = row
        if allowed_ip != client_ip:
            conn.rollback()
            return jsonify({"error": "Unauthorized"}), 401
        c.execute("DELETE FROM refresh_tokens WHERE token = ?", (token_hash,))
        if c.rowcount != 1:
            conn.rollback()
            return jsonify({"error": "Unauthorized"}), 401
        c.execute(
            "INSERT INTO refresh_tokens (token, user_id, expires, created, allowed_ip) VALUES (?, ?, ?, ?, ?)",
            (new_refresh_hash, user_id, new_expires, now_ts, allowed_ip)
        )
        conn.commit()

    # Issue new access token
    now = datetime.now(timezone.utc)
    access_payload = {
        "user_id": user_id,
        "username": username,
        "ip": client_ip,
        "iat": int(now.timestamp()),
        "jti": str(uuid.uuid4()),
        "exp": now + timedelta(seconds=ACCESS_TOKEN_DURATION),
        "aver": auth_version,
    }
    access_token = jwt.encode(access_payload, ACCESS_TOKEN_SECRET, algorithm="HS256")

    # Return response with new cookie
    response = make_response(jsonify({
        "access_token": access_token,
        "access_expires_in": ACCESS_TOKEN_DURATION
    }))
    response.set_cookie(
        "refresh_token",
        new_refresh_token,
        max_age=REFRESH_TOKEN_DURATION,
        path="/session",
        httponly=True,
        secure=True,
        samesite="Strict"
    )
    return response



# --- /logout ---
@app.route("/logout", methods=["POST"])
def logout():
    client_ip = get_client_ip()
    now_ts = int(time.time())

    # Endpoint protections
    is_ip_allowed(client_ip)
    rate_limit(client_ip, now_ts, PRIVATE_BUCKET, PRIVATE_REFILL_INTERVAL)
    payload = check_access_token()

    user_id = payload["user_id"]
    username = payload["username"]

    with db_write_lock, db_session() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM refresh_tokens WHERE user_id = ?", (user_id,))

    response = make_response(jsonify({"success": True, "message": "Logged out successfully"}))
    response.set_cookie(
        "refresh_token",
        "",
        max_age=0,
        path="/session",
        httponly=True,
        secure=True,
        samesite="Strict"
    )

    logger.info("[%s] [%s] User logged out", client_ip, username)

    return response



@app.route("/changepwd", methods=["POST"])
def change_password():
    client_ip = get_client_ip()
    now_ts = int(time.time())

    # Endpoint protections
    is_ip_allowed(client_ip)
    rate_limit(client_ip, now_ts, PRIVATE_BUCKET, PRIVATE_REFILL_INTERVAL)
    payload = check_access_token()

    user_id = payload["user_id"]
    username = payload["username"]

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not all(k in data for k in ["old_password", "new_password", "repeat_password"]):
        return jsonify({"error": "Invalid request"}), 400

    old_password = data["old_password"]
    new_password = data["new_password"]
    repeat_password = data["repeat_password"]

    if (
        not isinstance(old_password, str)
        or len(old_password.encode("utf-8")) > PASSWORD_MAX_BYTES
    ):
        return jsonify({"error": "Old password is incorrect"}), 400
    if new_password != repeat_password:
        return jsonify({"error": "New passwords do not match"}), 400
    valid, reason = validate_password_policy(new_password)
    if not valid:
        return jsonify({"error": reason}), 400

    # Fetch user and verify old password
    with db_session() as conn:
        c = conn.cursor()
        c.execute("SELECT password_hash FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if not row:
            return jsonify({"error": "User not found"}), 404

        password_hash = row[0]
        if not bcrypt.checkpw(old_password.encode(), password_hash):
            return jsonify({"error": "Old password is incorrect"}), 400

        new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt())
        c.execute(
            "UPDATE users SET password_hash = ?, auth_version = auth_version + 1 WHERE user_id = ?",
            (new_hash, user_id),
        )
        c.execute("DELETE FROM refresh_tokens WHERE user_id = ?", (user_id,))
        conn.commit()

    logger.info("[%s] [%s] User changed password", client_ip, username)

    return jsonify({"success": True, "message": "Password changed successfully"})



# --- Self-service profile ---
@app.route("/profile", methods=["POST"])
def update_profile():
    client_ip = get_client_ip()
    now_ts = int(time.time())

    is_ip_allowed(client_ip)
    rate_limit(client_ip, now_ts, PRIVATE_BUCKET, PRIVATE_REFILL_INTERVAL)
    payload = check_access_token()

    user_id = payload["user_id"]
    username = payload["username"]

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request"}), 400

    names_ok, names_error, first_name, last_name = parse_optional_names(data)
    if not names_ok:
        return jsonify({"error": names_error}), 400

    with db_write_lock, db_session() as conn:
        conn.execute(
            "UPDATE users SET first_name = ?, last_name = ? WHERE user_id = ?",
            (first_name, last_name, user_id),
        )
        conn.commit()

    logger.info("[%s] [%s] User updated their profile", client_ip, username)
    return jsonify({
        "success": True,
        "first_name": first_name,
        "last_name": last_name,
    })


@app.route("/profile/files", methods=["DELETE"])
def delete_own_files():
    client_ip = get_client_ip()
    now_ts = int(time.time())

    is_ip_allowed(client_ip)
    rate_limit(client_ip, now_ts, PRIVATE_BUCKET, PRIVATE_REFILL_INTERVAL)
    payload = check_access_token()

    user_id = payload["user_id"]
    username = payload["username"]

    with db_write_lock, db_session() as conn:
        file_ids = _collect_user_file_ids(conn, user_id)
        _purge_user_file_rows(conn, user_id)
        conn.commit()

    _remove_user_files_from_disk(user_id, file_ids)

    logger.info(
        "[%s] [%s] User deleted all their files (%d removed)",
        client_ip, username, len(file_ids),
    )
    return jsonify({"success": True, "deleted": len(file_ids)})


@app.route("/profile", methods=["DELETE"])
def delete_own_account():
    client_ip = get_client_ip()
    now_ts = int(time.time())

    is_ip_allowed(client_ip)
    rate_limit(client_ip, now_ts, PRIVATE_BUCKET, PRIVATE_REFILL_INTERVAL)
    payload = check_access_token()

    user_id = payload["user_id"]
    username = payload["username"]

    with db_write_lock, db_session() as conn:
        if _is_last_active_superadmin(conn, user_id):
            return jsonify({"error": "Cannot delete the last active superadmin"}), 400

        file_ids = _collect_user_file_ids(conn, user_id)
        # ON DELETE CASCADE clears links, reservations, upload state, and refresh
        # tokens; the collected file_ids drive the disk cleanup below.
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.commit()

    _remove_user_files_from_disk(user_id, file_ids)

    logger.info("[%s] [%s] User deleted their own account", client_ip, username)
    return jsonify({"success": True})



@app.route("/quota", methods=["GET"])
def get_quota():
    client_ip = get_client_ip()
    now_ts = int(time.time())

    # Endpoint protections
    is_ip_allowed(client_ip)
    rate_limit(client_ip, now_ts, PRIVATE_BUCKET, PRIVATE_REFILL_INTERVAL)
    payload = check_access_token()

    user_id = payload["user_id"]
    username = payload["username"]

    with db_session() as conn:
        c = conn.cursor()

        # Remove old quota reservations older than 4 hours
        c.execute("DELETE FROM quota_reservations WHERE user_id = ? AND created_at < ?", (user_id, now_ts - 14400))
        deleted = c.rowcount
        if deleted > 0:
            logger.info("[%s] [%s] Removed %s old quota reservation(s)", client_ip, username, deleted)

        # Sum of all uploaded files
        c.execute("SELECT SUM(size) FROM links WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        used_bytes = row[0] or 0 if row else 0

        # Sum of all current reservations
        c.execute("SELECT SUM(size) FROM quota_reservations WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        reserved_bytes = row[0] or 0 if row else 0

        # Check user's max_quota
        c.execute("SELECT quota_bytes FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        max_quota = row[0] if row else 0

    data = {
        "used": used_bytes + reserved_bytes,
        "reserved": reserved_bytes,
        "uploaded": used_bytes,
        "total": max_quota
    }
    return jsonify(data)



@app.route("/upload", methods=["POST"])
def upload_file():
    client_ip = request.remote_addr or "unknown"
    username = "anonymous"
    try:
        client_ip = get_client_ip()
        now_ts = int(time.time())

        # Endpoint protections
        is_ip_allowed(client_ip)
        rate_limit(client_ip, now_ts, PRIVATE_BUCKET, PRIVATE_REFILL_INTERVAL)
        payload = check_access_token()

        user_id = payload["user_id"]
        username = payload["username"]

        # Parse form data
        try:
            file = request.files['file']
            original_name = request.form['fileName']
            chunk_index = int(request.form['chunkIndex'])
            chunk_offset = int(request.form['chunkOffset'])
            total_chunks = int(request.form['totalChunks'])
            file_size = int(request.form['fileSize'])
            chunk_size = int(request.form['chunkSize'])
            requested_upload_id = request.form.get("uploadID", "")
            expires = int(request.form['expires'])
            if expires <= 0 or expires > MAX_LINK_EXPIRY:
                raise ValueError("Expiry is outside the allowed range")
            # Optional download cap set at upload time; blank means unlimited.
            max_downloads_raw = request.form.get('maxDownloads', '').strip()
            if max_downloads_raw == "":
                max_downloads = None
            else:
                max_downloads = int(max_downloads_raw)
                if max_downloads <= 0:
                    raise ValueError("Max downloads must be a positive integer")
            overwrite = int(request.form['overwrite'])
            chunk_data = file.stream.read(MAX_CHUNK_SIZE + 1)
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "Missing or invalid form data"}), 400

        valid, error, original_name = text_value(
            original_name, "File name", 255, required=True
        )
        if not valid:
            return jsonify({"error": error}), 400

        # Quota reservation variables
        total_size = file_size
        reservation_time = int(time.time())

        # Determine file path
        file_name = sanitize_filename(original_name)

        # Flag for the conditional pre-upload validation
        metadata_exists = False
        replace_file_id = None
        upload_state = "receiving"

        if requested_upload_id and not valid_owned_upload_id(user_id, requested_upload_id):
            return jsonify({"error": "Invalid upload ID"}), 400

        with db_write_lock, db_session() as conn:
            c = conn.cursor()
            if requested_upload_id:
                file_id = requested_upload_id
                c.execute(
                    """
                    SELECT file_name, total_chunks, total_size, chunk_size,
                           expires, replace_file_id, state
                    FROM upload_metadata
                    WHERE user_id = ? AND file_id = ?
                    """,
                    (user_id, file_id),
                )
                row = c.fetchone()
                metadata_exists = row is not None
                if row:
                    (
                        file_name, total_chunks, file_size, chunk_size,
                        expires, replace_file_id, upload_state,
                    ) = row
            else:
                file_id = f"{user_id}/{uuid.uuid4()}.part"

        if requested_upload_id and not metadata_exists:
            return jsonify({"error": "Upload not found"}), 404
        if not requested_upload_id and chunk_index != 0:
            return jsonify({"error": "The first request must contain chunk 0"}), 400
        if metadata_exists and chunk_size == 0:
            return jsonify({"error": "Legacy partial upload must be restarted"}), 409
        if not metadata_exists:
            expected_chunks = (
                (file_size + chunk_size - 1) // chunk_size
                if chunk_size > 0
                else 0
            )
            if (
                not file_name
                or file_size <= 0
                or chunk_size <= 0
                or chunk_size > MAX_CHUNK_SIZE
                or total_chunks <= 0
                or total_chunks != expected_chunks
                or overwrite not in (0, 1)
            ):
                return jsonify({"error": "Invalid upload manifest"}), 400

        if chunk_index < 0 or chunk_index >= total_chunks:
            return jsonify({"error": "Invalid chunk index"}), 400
        expected_offset = chunk_index * chunk_size
        expected_length = min(chunk_size, file_size - expected_offset)
        if chunk_offset != expected_offset or len(chunk_data) != expected_length:
            return jsonify({"error": "Chunk does not match upload manifest"}), 400

        # Pre-upload preparations are executed once
        filepath = resolve_upload_path(file_id)
        if not metadata_exists:

            # Check if file exists (the first time upload() function is called in the frontend, the overwrite flag is always 0)
            if overwrite == 0:
                with db_write_lock, db_session() as conn:
                    c = conn.cursor()
                    c.execute("SELECT file_id, size, hash, uploaded FROM links WHERE file_name = ? AND user_id = ?", (file_name, user_id,))
                    row = c.fetchone()

                    if row:
                        existing_file_id, existing_file_size_db, existing_file_hash, uploaded = row
                        existing_file_path = resolve_upload_path(existing_file_id)

                        try:
                            existing_file_size = os.path.getsize(existing_file_path)
                        except OSError:
                            existing_file_size = existing_file_size_db

                        # If a file exists in the database with the same name as the file being uploaded,
                        # the backend pass the details of the existing file to the frontend for the user
                        # to choose if it should be overwritten. If the user chooses 'Yes', the frontend
                        # re-call the upload() function with overwrite = 1 and the existing file details,
                        # to be deleted from the disk and database prior to the upload of the new file
                        return jsonify({
                            "exists": True,
                            "filename": file_name,
                            "fileID": existing_file_id,
                            "size": existing_file_size,
                            "hash": existing_file_hash,
                            "uploaded": uploaded
                        }), 409

            # If file exists and should be overwritten (this only runs when the upload() function is re-called with overwrite=1)
            if overwrite == 1:
                # Resolve overwrite authority from the authenticated user's row.
                with db_write_lock, db_session() as conn:
                    c = conn.cursor()
                    c.execute("SELECT file_id, size FROM links WHERE user_id = ? AND file_name = ?", (user_id, file_name,))
                    row = c.fetchone()
                    if row:
                        replace_file_id, existing_file_size = row
                        total_size = max(0, file_size - existing_file_size)
                    else:
                        replace_file_id = None
                        existing_file_size = 0
                        total_size = file_size  # fallback if no record

            with db_write_lock, db_session() as conn:
                c = conn.cursor()
                c.execute("BEGIN IMMEDIATE")

                # Remove old quota reservations older than 4 hours
                c.execute("DELETE FROM quota_reservations WHERE user_id = ? AND created_at < ?", (user_id, reservation_time - 14400,))

                # Check user's max_quota
                c.execute("SELECT quota_bytes FROM users WHERE user_id = ?", (user_id,))
                row = c.fetchone()
                max_quota = row[0] if row else 0

                # Compute current usage and reserved bytes
                c.execute("SELECT SUM(size) FROM links WHERE user_id = ?", (user_id, ))
                used_bytes = c.fetchone()[0] or 0
                c.execute("SELECT SUM(size) FROM quota_reservations WHERE user_id = ?", (user_id, ))
                reserved_bytes = c.fetchone()[0] or 0

                # Reject upload if quota would be exceeded
                if used_bytes + reserved_bytes + total_size > max_quota:
                    logger.warning("[%s] [%s] upload of file %s would exceed storage quota", client_ip, username, file_name)
                    return jsonify({"error": "Upload would exceed storage quota"}), 413

                # Reserve space for this upload
                c.execute("INSERT INTO quota_reservations (user_id, file_id, size, created_at) VALUES (?, ?, ?, ?)", (user_id, file_id, total_size, reservation_time))
                c.execute(
                    """
                    INSERT INTO upload_metadata
                        (user_id, file_id, file_name, total_chunks,
                         total_size, chunk_size, expires, replace_file_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id, file_id, file_name, total_chunks,
                        file_size, chunk_size, expires, replace_file_id,
                    ),
                )
                conn.commit()

        # Write chunk to disk
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        # Only write if this chunk hasn't been uploaded yet
        with db_write_lock, db_session() as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT 1 FROM upload_chunks
                WHERE user_id = ? AND file_id = ? AND chunk_index = ?
                """,
                (user_id, file_id, chunk_index),
            )
            chunk_exists = c.fetchone() is not None

        if not chunk_exists:
            try:
                # Open file in read/write binary mode, create if not exists
                with open(filepath, 'r+b') as f:
                    f.seek(chunk_offset)
                    f.write(chunk_data)
            except FileNotFoundError:
                filepath.parent.mkdir(parents=True, exist_ok=True)
                with open(filepath, 'wb') as f:
                    f.seek(chunk_offset)
                    f.write(chunk_data)
            except Exception as e:
                return internal_error_response(
                    "Upload failed to write chunk", client_ip, username, e
                )

            # Mark this chunk as uploaded
            with db_write_lock, db_session() as conn:
                c = conn.cursor()
                c.execute(
                    "INSERT OR IGNORE INTO upload_chunks (user_id, file_id, chunk_index, uploaded_at) VALUES (?, ?, ?, ?)",
                    (user_id, file_id, chunk_index, int(time.time()))
                )
                conn.commit()

        with db_write_lock, db_session() as conn:
            c = conn.cursor()
            c.execute("SELECT total_chunks FROM upload_metadata WHERE user_id = ? AND file_id = ?", (user_id, file_id))
            row = c.fetchone()
            total_chunks = row[0] if row else 0
            c.execute(
                "SELECT COUNT(*) FROM upload_chunks WHERE user_id = ? AND file_id = ?",
                (user_id, file_id),
            )
            uploaded_chunk_count = c.fetchone()[0]

        # Check if all chunks are uploaded and finalize
        if uploaded_chunk_count == total_chunks:
            # Remove .part from file_id and compute final path
            final_file_id = file_id.removesuffix(".part")
            final_filepath = resolve_upload_path(final_file_id)

            # Persist intent before moving bytes. A retry can now distinguish a
            # staged final object from an unrelated identifier collision.
            if upload_state == "receiving":
                try:
                    actual_size = filepath.stat().st_size
                except OSError as e:
                    return internal_error_response(
                        "Failed to inspect completed upload",
                        client_ip,
                        username,
                        e,
                        retry_upload_id=file_id,
                    )
                if actual_size != file_size:
                    return jsonify({"error": "Completed file size does not match manifest"}), 400
                with db_write_lock, db_session() as conn:
                    conn.execute(
                        """
                        UPDATE upload_metadata
                        SET state = 'finalizing'
                        WHERE user_id = ? AND file_id = ?
                        """,
                        (user_id, file_id),
                    )
                upload_state = "finalizing"

            # A finalizing row plus an existing final path is a durable retry
            # after the rename succeeded but the authority transaction failed.
            try:
                if filepath.exists():
                    if final_filepath.exists():
                        return jsonify({"error": "Upload identifier collision"}), 409
                    os.replace(filepath, final_filepath)
                elif not final_filepath.exists():
                    raise FileNotFoundError("Finalizing upload bytes are missing")
                if final_filepath.stat().st_size != file_size:
                    return jsonify({"error": "Completed file size does not match manifest"}), 400
                filepath = final_filepath
            except OSError as e:
                return internal_error_response(
                    "Failed to finalize upload",
                    client_ip,
                    username,
                    e,
                    retry_upload_id=file_id,
                )

            # Generate HMAC token for sharing
            uploaded = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            expire_timestamp = int(time.time()) + expires
            message = f"{final_file_id}:{expire_timestamp}".encode()
            link_token = base64.urlsafe_b64encode(hmac.new(SECRET_KEY, message, hashlib.sha256).digest()).decode().rstrip("=")
            full_link = f"{BASE_URL}/share/{link_token}"

            try:
                file_hash = hashlib.sha256()
                with final_filepath.open("rb") as uploaded_file:
                    for block in iter(lambda: uploaded_file.read(65536), b""):
                        file_hash.update(block)
            except OSError as e:
                return internal_error_response(
                    "Failed to hash finalized upload",
                    client_ip,
                    username,
                    e,
                    retry_upload_id=file_id,
                )

            # Commit the replacement only after the new bytes are complete and
            # hashed. If this transaction fails, the old link/file stay live.
            try:
                with db_write_lock, db_session() as conn:
                    c = conn.cursor()
                    c.execute("BEGIN IMMEDIATE")

                    if replace_file_id:
                        c.execute(
                            "DELETE FROM links WHERE user_id = ? AND file_id = ?",
                            (user_id, replace_file_id),
                        )
                    c.execute(
                        "INSERT INTO links (user_id, file_id, file_name, size, hash, uploaded, linktoken, expires, created, revoked, max_downloads) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            user_id, final_file_id, file_name, file_size,
                            file_hash.hexdigest(), uploaded, link_token,
                            expire_timestamp, uploaded, 0, max_downloads,
                        )
                    )
                    c.execute(
                        "DELETE FROM upload_chunks WHERE user_id = ? AND file_id = ?",
                        (user_id, file_id),
                    )
                    c.execute(
                        "DELETE FROM upload_metadata WHERE user_id = ? AND file_id = ?",
                        (user_id, file_id),
                    )
                    c.execute(
                        "DELETE FROM quota_reservations WHERE user_id = ? AND file_id = ?",
                        (user_id, file_id),
                    )
                    conn.commit()
            except Exception as e:
                return internal_error_response(
                    "Failed to commit finalized upload",
                    client_ip,
                    username,
                    e,
                    retry_upload_id=file_id,
                )

            if replace_file_id:
                old_path = resolve_upload_path(replace_file_id)
                try:
                    old_path.unlink(missing_ok=True)
                except OSError as e:
                    logger.error("[%s] [%s] Failed to remove replaced file %s: %s", client_ip, username, replace_file_id, e)

            logger.info("[%s] [%s] Successfully uploaded file %s (%s)", client_ip, username, file_name, final_file_id)
            return jsonify({"url": full_link, "fileID": file_id}), 200

        # For intermediate chunks
        return jsonify({
            "fileID": file_id,
            "chunk": chunk_index,
            "uploaded_chunk_count": uploaded_chunk_count,
            "total_chunks": total_chunks,
            "status": "ok"
        }), 200
    except HTTPException:
        raise
    except Exception as e:
        return internal_error_response("Upload failed", client_ip, username, e)



@app.route("/progress", methods=["GET"])
def upload_progress():
    client_ip = get_client_ip()
    now_ts = int(time.time())

    # Endpoint protections
    is_ip_allowed(client_ip)
    rate_limit(client_ip, now_ts, PRIVATE_BUCKET, PRIVATE_REFILL_INTERVAL)
    payload = check_access_token()

    user_id = payload["user_id"]
    username = payload["username"]

    file_id = request.headers.get("X-Upload-ID")
    if not valid_owned_upload_id(user_id, file_id):
        return jsonify({"error": "Missing or invalid upload ID"}), 400

    try:
        with db_write_lock, db_session() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT total_chunks, total_size, expires, state FROM upload_metadata WHERE user_id = ? AND file_id = ?",
                (user_id, file_id),
            )
            row = c.fetchone()
            if not row:
                return jsonify({"uploadedChunks": []}), 200

            total_chunks, total_size, expires, upload_state = row
            c.execute("SELECT chunk_index FROM upload_chunks WHERE user_id = ? AND file_id = ?", (user_id, file_id))
            rows = c.fetchall()
            uploaded_chunks = sorted(r[0] for r in rows)
            if upload_state == "finalizing" and uploaded_chunks:
                # Re-send one already-recorded chunk to drive idempotent
                # finalization recovery; the server will not rewrite its bytes.
                uploaded_chunks.remove(total_chunks - 1)

        return jsonify({
            "fileID": file_id,
            "uploadedChunks": uploaded_chunks,
            "totalChunks": total_chunks,
            "totalSize": total_size,
            "expires": expires
        }), 200

    except Exception as e:
        return internal_error_response(
            "Failed to read upload progress", client_ip, username, e
        )


@app.route("/cancel", methods=["POST"])
def cancel_upload():
    client_ip = get_client_ip()
    now_ts = int(time.time())

    # Endpoint protections
    is_ip_allowed(client_ip)
    rate_limit(client_ip, now_ts, PRIVATE_BUCKET, PRIVATE_REFILL_INTERVAL)
    payload = check_access_token()

    user_id = payload["user_id"]
    username = payload["username"]

    # Parse data from the backend
    data = request.get_json()
    file_id = data.get("uploadID") if isinstance(data, dict) else None
    if not valid_owned_upload_id(user_id, file_id):
        return jsonify({"error": "Missing or invalid upload ID"}), 400

    # Determine file_id
    with db_write_lock, db_session() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT file_id, state FROM upload_metadata WHERE user_id = ? AND file_id = ?",
            (user_id, file_id),
        )
        row = c.fetchone()
        if not row:
            return jsonify({"uploadedChunks": []}), 200
        _, upload_state = row

    try:
        # Delete partial file
        filepath = resolve_upload_path(file_id)
        if filepath.exists():
            filepath.unlink()
        if upload_state == "finalizing":
            resolve_upload_path(file_id.removesuffix(".part")).unlink(
                missing_ok=True
            )

        # Remove quota reservation
        with db_write_lock, db_session() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM quota_reservations WHERE user_id = ? AND file_id = ?", (user_id, file_id))
            conn.commit()

        with db_write_lock, db_session() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM upload_chunks WHERE user_id = ? AND file_id = ?", (user_id, file_id))
            c.execute("DELETE FROM upload_metadata WHERE user_id = ? AND file_id = ?", (user_id, file_id))
            conn.commit()

    except Exception as e:
        log_error = str(e)
        logger.error("[%s] [%s] Failed to clean canceled upload data: %s", client_ip, username, log_error)

    return jsonify({"upload status": "canceled"}), 200



@app.route("/links/<path:file_id>", methods=["GET"])
def get_link_detail(file_id):
    client_ip = get_client_ip()
    now_ts = int(time.time())

    # Endpoint protections
    is_ip_allowed(client_ip)
    rate_limit(client_ip, now_ts, PRIVATE_BUCKET, PRIVATE_REFILL_INTERVAL)
    payload = check_access_token()

    user_id = payload["user_id"]

    # Owner-scoped: only the caller's own rows are addressable.
    with db_session() as conn:
        row = conn.execute(
            f"SELECT {LINK_DICT_COLUMNS} FROM links WHERE user_id = ? AND file_id = ?",
            (user_id, file_id),
        ).fetchone()

    if not row:
        return jsonify({"error": "File not found"}), 404

    return jsonify(_link_row_to_dict(row))



@app.route("/links/<path:file_id>/settings", methods=["POST"])
def update_link_settings(file_id):
    client_ip = get_client_ip()
    now_ts = int(time.time())

    # Endpoint protections
    is_ip_allowed(client_ip)
    rate_limit(client_ip, now_ts, PRIVATE_BUCKET, PRIVATE_REFILL_INTERVAL)
    payload = check_access_token()

    user_id = payload["user_id"]
    username = payload["username"]

    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid request"}), 400

        # An expiry, when present, renews the HMAC token for a new expiry and
        # clears the revoked flag (this folds in the old /regenerate behaviour).
        set_expiry = data.get("expiry") is not None
        expires = None
        if set_expiry:
            try:
                expires = int(data.get("expiry"))
            except (TypeError, ValueError):
                return jsonify({"error": "Expiry must be an integer"}), 400
            # Validate the range before any lookup so a bad value is rejected
            # regardless of whether the file exists.
            if expires <= 0 or expires > MAX_LINK_EXPIRY:
                return jsonify({"error": "Expiry is outside the allowed range"}), 400

        # max_downloads is only touched when its key is present. Null/blank means
        # unlimited; a positive int caps downloads. A cap at or below the current
        # count is allowed — the link is then simply exhausted.
        set_max = "max_downloads" in data
        max_downloads = None
        if set_max:
            raw_max = data.get("max_downloads")
            if raw_max is None or raw_max == "":
                max_downloads = None
            else:
                try:
                    max_downloads = int(raw_max)
                except (TypeError, ValueError):
                    return jsonify({"error": "Max downloads must be an integer"}), 400
                if max_downloads <= 0:
                    return jsonify({"error": "Max downloads must be a positive integer"}), 400

        # notes are only touched when the key is present; a blank string clears.
        set_notes = "notes" in data
        notes = ""
        if set_notes:
            valid, error, notes = text_value(data.get("notes"), "Notes", NOTES_MAX)
            if not valid:
                return jsonify({"error": error}), 400
            notes = notes or ""   # text_value yields None for a null; column is NOT NULL

        with db_write_lock, db_session() as conn:
            c = conn.cursor()
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "SELECT file_id FROM links WHERE user_id = ? AND file_id = ?",
                (user_id, file_id),
            )
            if not c.fetchone():
                conn.rollback()
                return jsonify({"error": "File not found"}), 404

            if set_expiry:
                expire_timestamp = now_ts + expires
                message = f"{file_id}:{expire_timestamp}".encode()
                link_token = base64.urlsafe_b64encode(hmac.new(SECRET_KEY, message, hashlib.sha256).digest()).decode().rstrip("=")
                created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                c.execute(
                    "UPDATE links SET linktoken = ?, expires = ?, created = ?, revoked = 0 "
                    "WHERE user_id = ? AND file_id = ?",
                    (link_token, expire_timestamp, created, user_id, file_id),
                )

            if set_max:
                c.execute(
                    "UPDATE links SET max_downloads = ? WHERE user_id = ? AND file_id = ?",
                    (max_downloads, user_id, file_id),
                )

            if set_notes:
                c.execute(
                    "UPDATE links SET notes = ? WHERE user_id = ? AND file_id = ?",
                    (notes, user_id, file_id),
                )

            row = c.execute(
                f"SELECT {LINK_DICT_COLUMNS} FROM links WHERE user_id = ? AND file_id = ?",
                (user_id, file_id),
            ).fetchone()
            conn.commit()

        logger.info("[%s] [%s] Link settings updated for %s", client_ip, username, file_id)
        return jsonify(_link_row_to_dict(row))

    except Exception as e:
        return internal_error_response(
            "Failed to update link settings", client_ip, username, e
        )



@app.route("/links/<path:file_id>/revoke", methods=["POST"])
def revoke_file(file_id):
    client_ip = get_client_ip()
    now_ts = int(time.time())

    # Endpoint protections
    is_ip_allowed(client_ip)
    rate_limit(client_ip, now_ts, PRIVATE_BUCKET, PRIVATE_REFILL_INTERVAL)
    payload = check_access_token()

    user_id = payload["user_id"]
    username = payload["username"]

    try:
        with db_write_lock, db_session() as conn:
            c = conn.cursor()
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "SELECT file_id FROM links WHERE user_id = ? AND file_id = ?",
                (user_id, file_id),
            )
            if not c.fetchone():
                conn.rollback()
                return jsonify({"error": "File not found"}), 404

            c.execute(
                "UPDATE links SET revoked = 1 WHERE user_id = ? AND file_id = ?",
                (user_id, file_id),
            )
            row = c.execute(
                f"SELECT {LINK_DICT_COLUMNS} FROM links WHERE user_id = ? AND file_id = ?",
                (user_id, file_id),
            ).fetchone()
            conn.commit()

        logger.info("[%s] [%s] Link revoked for file: %s", client_ip, username, file_id)
        return jsonify(_link_row_to_dict(row))

    except Exception as e:
        return internal_error_response(
            "Failed to revoke link", client_ip, username, e
        )



@app.route("/links/<path:file_id>", methods=["DELETE"])
def delete_file(file_id):
    client_ip = get_client_ip()
    now_ts = int(time.time())

    # Endpoint protections
    is_ip_allowed(client_ip)
    rate_limit(client_ip, now_ts, PRIVATE_BUCKET, PRIVATE_REFILL_INTERVAL)
    payload = check_access_token()

    user_id = payload["user_id"]
    username = payload["username"]

    try:
        # Remove the authority row first; a crash can leave only an inaccessible
        # orphan for the age-aware sweeper, never a live row pointing at no bytes.
        with db_write_lock, db_session() as conn:
            c = conn.cursor()
            c.execute(
                "DELETE FROM links WHERE user_id = ? AND file_id = ?",
                (user_id, file_id),
            )
            deleted = c.rowcount
            conn.commit()

        if not deleted:
            return jsonify({"error": "File not found"}), 404

        try:
            resolve_upload_path(file_id).unlink(missing_ok=True)
        except (OSError, ValueError) as e:
            logger.error("[%s] [%s] Deferred orphan cleanup for %s: %s", client_ip, username, file_id, e)

        logger.info("[%s] [%s] File deleted: %s", client_ip, username, file_id)
        return jsonify({"success": True})

    except Exception as e:
        return internal_error_response(
            "Failed to delete file", client_ip, username, e
        )



# --- Admin endpoints ---

def _admin_gate():
    client_ip = get_client_ip()
    now_ts = int(time.time())
    is_ip_allowed(client_ip)
    rate_limit(client_ip, now_ts, PRIVATE_BUCKET, PRIVATE_REFILL_INTERVAL)
    payload = check_access_token()
    require_admin(payload)
    return payload, client_ip


@app.route("/admin/users", methods=["GET"])
def admin_list_users():
    payload, client_ip = _admin_gate()
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT user_id, username, role, status, quota_bytes, created,
                   first_name, last_name,
                   (SELECT COALESCE(SUM(size), 0) FROM links WHERE links.user_id = users.user_id),
                   (SELECT COUNT(*) FROM links WHERE links.user_id = users.user_id)
            FROM users
            """
        ).fetchall()
    users = []
    for r in rows:
        users.append({
            "user_id": r[0],
            "username": r[1],
            "role": r[2],
            "status": r[3],
            "quota_bytes": r[4],
            "created": r[5],
            "first_name": r[6],
            "last_name": r[7],
            "used_bytes": r[8],
            "file_count": r[9],
        })
    # Natural (sequential) order so 'user2' sorts before 'user10'.
    users.sort(key=lambda u: [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", u["username"])
    ])
    return jsonify(users)


@app.route("/admin/users", methods=["POST"])
def admin_create_user():
    payload, client_ip = _admin_gate()
    admin_name = payload["username"]

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request"}), 400

    valid, username, username_error = validate_username(data.get("username"))
    if not valid:
        return jsonify({"error": username_error}), 400

    password = data.get("password")
    valid_pw, reason = validate_password_policy(password)
    if not valid_pw:
        return jsonify({"error": reason}), 400

    quota_bytes = data.get("quota_bytes")
    if not isinstance(quota_bytes, int) or isinstance(quota_bytes, bool) or quota_bytes <= 0:
        return jsonify({"error": "Quota must be a positive number of bytes"}), 400

    # 'superadmin' is never grantable over the web (CLI-only tier); a regular
    # admin may only create regular users, while a superadmin may also mint admins.
    role = data.get("role", "user")
    if role not in ("admin", "user"):
        return jsonify({"error": "Role must be 'admin' or 'user'"}), 400
    if role == "admin" and not _is_super(payload):
        return jsonify({"error": "Only a superadmin can create admins"}), 403

    names_ok, names_error, first_name, last_name = parse_optional_names(data)
    if not names_ok:
        return jsonify({"error": names_error}), 400

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
    user_id = str(uuid.uuid4())
    created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        with db_write_lock, db_session() as conn:
            conn.execute(
                """
                INSERT INTO users
                    (user_id, username, first_name, last_name,
                     password_hash, role, quota_bytes, created)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, username, first_name, last_name,
                 password_hash, role, quota_bytes, created),
            )
            conn.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": "Username already exists"}), 409

    logger.info("[%s] [%s] %s created user '%s' (role=%s)", client_ip, admin_name, payload["_role"].capitalize(), username, role)
    return jsonify({"success": True, "user_id": user_id})


@app.route("/admin/users/<target_id>", methods=["DELETE"])
def admin_delete_user(target_id):
    payload, client_ip = _admin_gate()
    admin_name = payload["username"]

    if target_id == payload["user_id"]:
        return jsonify({"error": "Cannot delete yourself"}), 400

    with db_write_lock, db_session() as conn:
        row = conn.execute(
            "SELECT username, role FROM users WHERE user_id = ?", (target_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "User not found"}), 404
        target_name, target_role = row

        guard = _guard_admin_target(payload, target_role)
        if guard:
            return guard
        if _is_last_active_superadmin(conn, target_id):
            return jsonify({"error": "Cannot delete the last active superadmin"}), 400

        file_ids = _collect_user_file_ids(conn, target_id)

        # ON DELETE CASCADE clears the user's links, reservations, and upload
        # state rows; we only need the file_ids for the disk cleanup below.
        conn.execute("DELETE FROM users WHERE user_id = ?", (target_id,))
        conn.commit()

    _remove_user_files_from_disk(target_id, file_ids)

    logger.info("[%s] [%s] %s deleted user '%s'", client_ip, admin_name, payload["_role"].capitalize(), target_name)
    return jsonify({"success": True})


@app.route("/admin/users/<target_id>/files", methods=["DELETE"])
def admin_delete_user_files(target_id):
    payload, client_ip = _admin_gate()
    admin_name = payload["username"]

    with db_write_lock, db_session() as conn:
        row = conn.execute(
            "SELECT username, role FROM users WHERE user_id = ?", (target_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "User not found"}), 404
        target_name, target_role = row

        guard = _guard_admin_target(payload, target_role)
        if guard:
            return guard

        file_ids = _collect_user_file_ids(conn, target_id)
        # The account row is deliberately left intact so the user keeps their
        # login and quota; only the files and upload state go.
        _purge_user_file_rows(conn, target_id)
        conn.commit()

    _remove_user_files_from_disk(target_id, file_ids)

    logger.info(
        "[%s] [%s] %s purged all files for user '%s' (%d removed)",
        client_ip, admin_name, payload["_role"].capitalize(), target_name, len(file_ids),
    )
    return jsonify({"success": True, "deleted": len(file_ids)})


@app.route("/admin/users/<target_id>/status", methods=["POST"])
def admin_set_status(target_id):
    payload, client_ip = _admin_gate()
    admin_name = payload["username"]

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request"}), 400

    status = data.get("status")
    if status not in ("active", "disabled"):
        return jsonify({"error": "Status must be 'active' or 'disabled'"}), 400

    with db_write_lock, db_session() as conn:
        row = conn.execute(
            "SELECT username, role FROM users WHERE user_id = ?", (target_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "User not found"}), 404

        guard = _guard_admin_target(payload, row[1])
        if guard:
            return guard
        if status == "disabled" and _is_last_active_superadmin(conn, target_id):
            return jsonify({"error": "Cannot disable the last active superadmin"}), 400

        conn.execute(
            "UPDATE users SET status = ?, auth_version = auth_version + 1 WHERE user_id = ?",
            (status, target_id),
        )
        if status == "disabled":
            conn.execute(
                "DELETE FROM refresh_tokens WHERE user_id = ?", (target_id,)
            )
        conn.commit()

    logger.info("[%s] [%s] %s set user '%s' status=%s", client_ip, admin_name, payload["_role"].capitalize(), row[0], status)
    return jsonify({"success": True})


@app.route("/admin/users/<target_id>/role", methods=["POST"])
def admin_set_role(target_id):
    payload, client_ip = _admin_gate()
    admin_name = payload["username"]

    # Promotion/demotion is a superadmin-only lever, and it only ever moves an
    # account between 'user' and 'admin'. The superadmin tier is CLI-only: it is
    # never a valid target role, and an existing superadmin's role is immutable
    # here, so no web path can mint or unseat a superadmin.
    if not _is_super(payload):
        return jsonify({"error": "Only a superadmin can change roles"}), 403

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request"}), 400

    role = data.get("role")
    if role not in ("admin", "user"):
        return jsonify({"error": "Role must be 'admin' or 'user'"}), 400

    with db_write_lock, db_session() as conn:
        row = conn.execute(
            "SELECT username, role FROM users WHERE user_id = ?", (target_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "User not found"}), 404

        if row[1] == "superadmin":
            return jsonify({"error": "A superadmin's role can only be changed via the CLI"}), 403

        conn.execute(
            "UPDATE users SET role = ? WHERE user_id = ?", (role, target_id)
        )
        conn.commit()

    logger.info("[%s] [%s] %s set user '%s' role=%s", client_ip, admin_name, payload["_role"].capitalize(), row[0], role)
    return jsonify({"success": True})


@app.route("/admin/users/<target_id>/password", methods=["POST"])
def admin_reset_password(target_id):
    payload, client_ip = _admin_gate()
    admin_name = payload["username"]

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request"}), 400

    password = data.get("password")
    valid_pw, reason = validate_password_policy(password)
    if not valid_pw:
        return jsonify({"error": reason}), 400

    new_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt())

    with db_write_lock, db_session() as conn:
        row = conn.execute(
            "SELECT username, role FROM users WHERE user_id = ?", (target_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "User not found"}), 404

        guard = _guard_admin_target(payload, row[1])
        if guard:
            return guard

        conn.execute(
            "UPDATE users SET password_hash = ?, auth_version = auth_version + 1 WHERE user_id = ?",
            (new_hash, target_id),
        )
        conn.execute(
            "DELETE FROM refresh_tokens WHERE user_id = ?", (target_id,)
        )
        conn.commit()

    logger.info("[%s] [%s] %s reset password for '%s'", client_ip, admin_name, payload["_role"].capitalize(), row[0])
    return jsonify({"success": True})


@app.route("/admin/users/<target_id>/quota", methods=["POST"])
def admin_set_quota(target_id):
    payload, client_ip = _admin_gate()
    admin_name = payload["username"]

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request"}), 400

    quota_bytes = data.get("quota_bytes")
    if not isinstance(quota_bytes, int) or isinstance(quota_bytes, bool) or quota_bytes <= 0:
        return jsonify({"error": "Quota must be a positive number of bytes"}), 400

    with db_write_lock, db_session() as conn:
        row = conn.execute(
            "SELECT username, role FROM users WHERE user_id = ?", (target_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "User not found"}), 404

        guard = _guard_admin_target(payload, row[1])
        if guard:
            return guard

        conn.execute(
            "UPDATE users SET quota_bytes = ? WHERE user_id = ?",
            (quota_bytes, target_id),
        )
        conn.commit()

    logger.info("[%s] [%s] %s set quota for '%s' to %d bytes", client_ip, admin_name, payload["_role"].capitalize(), row[0], quota_bytes)
    return jsonify({"success": True})


# Launch server
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, threaded=True)
