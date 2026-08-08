#!/usr/bin/env bash
set -euo pipefail

# Deploy from a trusted checkout:
#   sudo ./install.sh --keep-data
#   sudo ./install.sh --wipe-data
#
# There is deliberately no data-mode default. --keep-data never removes runtime
# state; --wipe-data removes only the fixed /var/lib/uplivion database/share targets.

MODE=""
for argument in "$@"; do
    case "$argument" in
        --keep-data) MODE="keep" ;;
        --wipe-data) MODE="wipe" ;;
        *)
            echo "Unknown argument: $argument" >&2
            exit 2
            ;;
    esac
done
if [ -z "$MODE" ]; then
    echo "Usage: sudo ./install.sh --keep-data|--wipe-data" >&2
    exit 2
fi
if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root." >&2
    exit 1
fi

SOURCE="$(cd "$(dirname "$0")" && pwd)"
APP="/var/www/uplivion"
STATE="/var/lib/uplivion"
SHARE="$STATE/share"
DB="$STATE/store.db"
ENV_DIR="/etc/uplivion"
ENV_FILE="$ENV_DIR/uplivion.env"
if [ "$SOURCE" = "$APP" ]; then
    echo "Run install.sh from a separate trusted checkout, not $APP." >&2
    exit 1
fi

for binary in adduser curl getent nginx openssl python3 systemctl; do
    command -v "$binary" >/dev/null || {
        echo "ERROR: $binary is not installed" >&2
        exit 1
    }
done
python3 -c "import venv" >/dev/null 2>&1 || {
    echo "ERROR: python3-venv is not installed" >&2
    exit 1
}

DEPLOY_PATHS=(
    server.py
    create_users.py
    request_validation.py
    username_validation.py
    store_schema.py
    requirements.in
    requirements.txt
    requirements-test.txt
    DEPENDENCY_LOCK.md
    public
    install.sh
    uplivion.service
    uplivion.env.example
)
for path in "${DEPLOY_PATHS[@]}"; do
    if [ ! -e "$SOURCE/$path" ]; then
        echo "ERROR: required deploy path is missing: $path" >&2
        exit 1
    fi
done

REAL_ENV="$SOURCE/uplivion.env"
if [ ! -f "$REAL_ENV" ]; then
    echo "ERROR: $REAL_ENV is missing." >&2
    echo "Copy uplivion.env.example to uplivion.env and fill in the CHANGE_ME fields." >&2
    exit 1
fi
if grep -q 'CHANGE_ME' "$REAL_ENV"; then
    echo "ERROR: $REAL_ENV still has CHANGE_ME placeholders. Fill in real values before installing." >&2
    exit 1
fi
set -a
# shellcheck disable=SC1090
source "$REAL_ENV"
set +a

if ! getent passwd uplivion >/dev/null; then
    adduser uplivion --disabled-password --gecos "" --shell /usr/sbin/nologin
fi

install -d -m 0750 -o root -g uplivion "$ENV_DIR"
if [ ! -f "$ENV_FILE" ]; then
    install -m 0640 -o root -g uplivion "$REAL_ENV" "$ENV_FILE"
    hmac_secret="$(openssl rand -hex 32)"
    access_secret="$(openssl rand -hex 32)"
    sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$hmac_secret/" "$ENV_FILE"
    sed -i "s/^ACCESS_TOKEN_SECRET=.*/ACCESS_TOKEN_SECRET=$access_secret/" "$ENV_FILE"
    unset hmac_secret access_secret
    echo "Created $ENV_FILE; review the IP allowlist."
fi

install -d -m 0755 -o root -g root "$APP"
if [ ! -x "$APP/venv/bin/python" ]; then
    python3 -m venv "$APP/venv"
fi
"$APP/venv/bin/pip" install --require-hashes -r "$SOURCE/requirements.txt"
"$APP/venv/bin/python" --version
sha256sum "$SOURCE/requirements.txt"

systemctl stop uplivion.service 2>/dev/null || true
if [ "$MODE" = "wipe" ]; then
    rm -f "$DB" "$DB-wal" "$DB-shm"
    if [ -d "$SHARE" ]; then
        find "$SHARE" -mindepth 1 -delete
    fi
    echo "Wiped Uplivion runtime state under $STATE."
fi
install -d -m 0750 -o uplivion -g www-data "$STATE"
install -d -m 2750 -o uplivion -g www-data "$SHARE"

# Wipe everything under the app tree except venv (guarded above so a normal
# deploy doesn't pay to recreate it) before repopulating from DEPLOY_PATHS.
# Nothing else under $APP is precious — real state lives under $STATE — so
# this needs no per-path retirement tracking that can fall out of sync.
find "$APP" -mindepth 1 -maxdepth 1 ! -name venv -exec rm -rf {} +
for path in "${DEPLOY_PATHS[@]}"; do
    cp -a "$SOURCE/$path" "$APP/$path"
done
chown -R root:root "$APP"
find "$APP" -type d -exec chmod go-w {} +
find "$APP" -type f -exec chmod go-w {} +

install -m 0644 -o root -g root "$SOURCE/uplivion.service" \
    /etc/systemd/system/uplivion.service
systemctl daemon-reload
systemctl enable uplivion.service

install -d -m 0755 -o root -g root /etc/nginx/snippets
ALLOWLIST_RENDERED="$(mktemp)"
{
    echo "# Generated from UPLIVION_ALLOWED_IP_RANGES in uplivion.env at install"
    echo "# time. Edit uplivion.env and re-run install.sh, not this file."
    IFS=', ' read -ra ip_ranges <<< "$UPLIVION_ALLOWED_IP_RANGES"
    for range in "${ip_ranges[@]}"; do
        [ -n "$range" ] && echo "allow $range;"
    done
    echo "deny all;"
} > "$ALLOWLIST_RENDERED"
install -m 0644 -o root -g root "$ALLOWLIST_RENDERED" \
    /etc/nginx/snippets/uplivion-private-allowlist.conf
rm -f "$ALLOWLIST_RENDERED"

VHOST_RENDERED="$(mktemp)"
sed \
    -e "s/__PUBLIC_DOMAIN__/$UPLIVION_PUBLIC_DOMAIN/g" \
    -e "s/__LAN_DOMAIN__/$UPLIVION_LAN_DOMAIN/g" \
    -e "s/__CERT_DOMAIN__/$UPLIVION_CERT_DOMAIN/g" \
    "$SOURCE/uplivion.nginx" > "$VHOST_RENDERED"
if grep -qE '__[A-Z_]+__' "$VHOST_RENDERED"; then
    echo "ERROR: rendered nginx vhost still has an unfilled placeholder token." >&2
    rm -f "$VHOST_RENDERED"
    exit 1
fi

NGINX_SITE="/etc/nginx/sites-available/uplivion"
NGINX_BACKUP=""
if [ -f "$NGINX_SITE" ]; then
    NGINX_BACKUP="$(mktemp)"
    cp "$NGINX_SITE" "$NGINX_BACKUP"
fi
install -m 0644 -o root -g root "$VHOST_RENDERED" "$NGINX_SITE"
rm -f "$VHOST_RENDERED"
ln -sfn "$NGINX_SITE" /etc/nginx/sites-enabled/uplivion
if ! nginx -t; then
    if [ -n "$NGINX_BACKUP" ]; then
        cp "$NGINX_BACKUP" "$NGINX_SITE"
    else
        rm -f "$NGINX_SITE" /etc/nginx/sites-enabled/uplivion
    fi
    rm -f "$NGINX_BACKUP"
    echo "ERROR: nginx rejected the candidate; previous site restored." >&2
    exit 1
fi
rm -f "$NGINX_BACKUP"
systemctl reload-or-restart nginx

if command -v fail2ban-client >/dev/null; then
    install -d -m 0755 -o root -g root /etc/fail2ban/filter.d /etc/fail2ban/jail.d
    for filter in "$SOURCE"/fail2ban/filters/uplivion-*.local; do
        install -m 0644 -o root -g root "$filter" "/etc/fail2ban/filter.d/$(basename "$filter")"
    done
    install -m 0644 -o root -g root \
        "$SOURCE/fail2ban/uplivion-jail.local" \
        /etc/fail2ban/jail.d/uplivion-jail.local
    if fail2ban-client -t >/dev/null 2>&1; then
        systemctl reload-or-restart fail2ban
        echo "Installed and reloaded Uplivion fail2ban jails."
    else
        echo "WARNING: fail2ban rejected the candidate config; jails written but not reloaded." >&2
        echo "         Inspect with: fail2ban-client -t" >&2
    fi
else
    echo "fail2ban not found on this host — skipping jail install (see fail2ban/ to add it later)."
fi

systemctl restart uplivion.service
for attempt in 1 2 3 4 5; do
    if systemctl is-active --quiet uplivion.service \
        && curl --silent --output /dev/null --max-time 2 \
            --header "Content-Type: application/json" \
            --data '{}' http://127.0.0.1:8000/login; then
        echo "Uplivion deployed successfully ($MODE data)."
        exit 0
    fi
    sleep 1
done

echo "ERROR: Uplivion did not pass its loopback liveness check." >&2
echo "Inspect: journalctl -u uplivion.service -n 100" >&2
exit 1
