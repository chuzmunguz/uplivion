#!/usr/bin/env bash
set -euo pipefail

# Reverse of install.sh. Removes only the artifacts install.sh creates and
# never touches shared system state (nginx core config, other sites,
# letsencrypt certificates, the global fail2ban ignoreip/jail.local,
# WireGuard) — but DOES remove Uplivion's own fail2ban jail.d/filter.d
# drop-ins if install.sh put them there.
#
#   sudo ./uninstall.sh            # remove app + service + nginx wiring;
#                                  # KEEP data, secrets, and the uplivion user
#   sudo ./uninstall.sh --purge    # additionally remove /var/lib/uplivion data,
#                                  # /etc/uplivion secrets, and the uplivion account
#
# There is deliberately no data removal without --purge: dropped databases and
# uploaded files are not recoverable.

PURGE=0
for argument in "$@"; do
    case "$argument" in
        --purge) PURGE=1 ;;
        -h|--help)
            sed -n '4,14p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown argument: $argument" >&2
            echo "Usage: sudo ./uninstall.sh [--purge]" >&2
            exit 2
            ;;
    esac
done
if [ "$(id -u)" -ne 0 ]; then
    echo "Run this uninstaller as root." >&2
    exit 1
fi

APP="/var/www/uplivion"
STATE="/var/lib/uplivion"
ENV_DIR="/etc/uplivion"
UNIT="/etc/systemd/system/uplivion.service"
NGINX_SITE="/etc/nginx/sites-available/uplivion"
NGINX_LINK="/etc/nginx/sites-enabled/uplivion"
NGINX_SNIPPETS=(
    /etc/nginx/snippets/uplivion-private-allowlist.conf
)

# --- systemd unit -----------------------------------------------------------
if [ -f "$UNIT" ] || systemctl list-unit-files uplivion.service >/dev/null 2>&1; then
    systemctl disable --now uplivion.service >/dev/null 2>&1 || true
    rm -f "$UNIT"
    systemctl daemon-reload
    systemctl reset-failed uplivion.service >/dev/null 2>&1 || true
    echo "Removed uplivion.service."
fi

# --- nginx ------------------------------------------------------------------
# Remove the site first, then the snippets it includes, so an intermediate
# nginx -t never sees a site referencing a deleted snippet.
nginx_changed=0
for target in "$NGINX_LINK" "$NGINX_SITE" "${NGINX_SNIPPETS[@]}"; do
    if [ -e "$target" ] || [ -L "$target" ]; then
        rm -f "$target"
        nginx_changed=1
    fi
done
if [ "$nginx_changed" -eq 1 ]; then
    echo "Removed uplivion nginx site and snippets."
    if command -v nginx >/dev/null; then
        if nginx -t >/dev/null 2>&1; then
            systemctl reload nginx || true
            echo "Reloaded nginx."
        else
            echo "WARNING: nginx -t failed after removing uplivion config; not reloading." >&2
            echo "         Inspect with: nginx -t" >&2
        fi
    fi
fi

# --- application code (no runtime state lives here) -------------------------
if [ -d "$APP" ]; then
    rm -rf "$APP"
    echo "Removed application directory $APP."
fi

# --- fail2ban (only the jail.d/filter.d drop-ins install.sh may have added) -
FAIL2BAN_JAIL="/etc/fail2ban/jail.d/uplivion-jail.local"
if [ -f "$FAIL2BAN_JAIL" ] || compgen -G "/etc/fail2ban/filter.d/uplivion-*.local" >/dev/null; then
    rm -f "$FAIL2BAN_JAIL" /etc/fail2ban/filter.d/uplivion-*.local
    echo "Removed Uplivion fail2ban jail/filters."
    if command -v fail2ban-client >/dev/null; then
        if fail2ban-client -t >/dev/null 2>&1; then
            systemctl reload-or-restart fail2ban 2>/dev/null || true
            echo "Reloaded fail2ban."
        else
            echo "WARNING: fail2ban -t failed after removing Uplivion config; not reloading." >&2
            echo "         Inspect with: fail2ban-client -t" >&2
        fi
    fi
fi

# --- data, secrets, service account (destructive; --purge only) -------------
if [ "$PURGE" -eq 1 ]; then
    if [ -d "$STATE" ]; then
        rm -rf "$STATE"
        echo "Purged runtime state $STATE (database and uploaded files)."
    fi
    if [ -d "$ENV_DIR" ]; then
        rm -rf "$ENV_DIR"
        echo "Purged secrets $ENV_DIR."
    fi
    if getent passwd uplivion >/dev/null; then
        deluser --remove-home uplivion >/dev/null 2>&1 || userdel -r uplivion >/dev/null 2>&1 || true
        echo "Removed the uplivion service account."
    fi
else
    echo
    echo "Kept (remove with --purge):"
    [ -d "$STATE" ] && echo "  $STATE   (database + uploaded files)"
    [ -d "$ENV_DIR" ] && echo "  $ENV_DIR       (secrets: SECRET_KEY, ACCESS_TOKEN_SECRET)"
    getent passwd uplivion >/dev/null && echo "  service account 'uplivion'"
fi

# --- artifacts install.sh never created, so uninstall never removes ---------
echo
echo "Not touched (install.sh did not create these — remove by hand if unused):"
echo "  the fail2ban package itself, and its global jail.local/ignoreip"
echo "  WireGuard peer configuration"
echo "  TLS certificates under /etc/letsencrypt"
echo "  nginx access/error logs /var/log/nginx/uplivion-*.log"
echo
echo "Uplivion uninstalled."
