# Uplivion

Uplivion is a privacy-focused, self-hosted file-sharing service with
resumable uploads, expiring share links, per-user quotas, and a three-tier
role system.

Your files stay on your own server. No third-party cloud, no tracking, no
analytics. Share links are time-limited and revocable, downloads can be
capped, and the admin interface is restricted to a private network range.
Authentication uses short-lived JWT access tokens and single-use
refresh-token rotation in HTTP-only cookies. Uploads and links are isolated
per user.

Three roles form a strict hierarchy: **superadmins** can manage every
account including other superadmins, **admins** can create, disable, and
delete regular users through the web panel but cannot touch higher roles,
and **users** manage their own files and quota.

Features:

- resumable chunked uploads with pause, cancel, and overwrite;
- file manager with card layout, per-file settings overlay, and bulk
  actions;
- share links with expiry, download limits, download counts, and
  per-file notes;
- self-service profile for display name, password, and account deletion;
- admin web panel for user management;
- per-user storage quotas with reservation on upload;
- installable as a PWA on mobile and desktop;
- SQLite storage, in-process rate limiting, and optional fail2ban
  integration.

## Install

The install script handles the venv, dependencies, nginx vhosts, systemd unit,
and optional fail2ban jails. The public vhost expects a Let's Encrypt certificate
for your domain.

1. Clone this repo somewhere other than `/var/www/uplivion`. The install
   script refuses to run from its own deploy target.
2. Copy `uplivion.env.example` to `uplivion.env` (gitignored) and fill in
   the `CHANGE_ME` fields (see the table below). Leave `SECRET_KEY` and
   `ACCESS_TOKEN_SECRET` as they are; `install.sh` overwrites them with
   random 32-byte secrets on first install.
3. Run `sudo ./install.sh --wipe-data` (fresh install) or `--keep-data`
   (redeploy onto existing `/var/lib/uplivion` state). This creates the
   `uplivion` system user, builds the venv, installs hash-pinned
   dependencies, renders and enables the nginx vhosts, installs the
   systemd unit and (if present) the fail2ban jails, and starts the
   service.
4. Re-run `install.sh` after changing the env file so nginx/systemd pick up
   the new values.
5. Optional: `sudo ./seed.sh` after `--wipe-data` creates a handful of test
   accounts (no files or links) for local testing.

## Creating accounts

The web admin panel can create `admin` and `user` accounts but not
`superadmin`. That role can only be assigned through the operator CLI,
run on the host as the `uplivion` service user:

```sh
sudo -u uplivion /var/www/uplivion/venv/bin/python3 /var/www/uplivion/create_users.py
```

If you enter a username that already exists, it asks whether to replace it.
Replacing reuses the account's existing `user_id`, so all of that user's
files and share links stay intact. It resets the password, quota, and names
to whatever you enter, forces re-login everywhere, and leaves the role untouched.

## Uninstall   

To remove:

- `sudo ./uninstall.sh` removes the service but keeps data and the
  `uplivion` service user account.
- `sudo ./uninstall.sh --purge` also removes `/var/lib/uplivion` data,
  `/etc/uplivion` secrets, and the service account.
  
## Environment file fields

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | HMAC key for share-link tokens. Auto-generated on first install. |
| `ACCESS_TOKEN_SECRET` | JWT signing secret. Auto-generated on first install. |
| `UPLIVION_UPLOAD_DIR` | Where uploaded files are stored. Defaults to `/var/lib/uplivion/share`. |
| `UPLIVION_DB_PATH` | SQLite database path. Defaults to `/var/lib/uplivion/store.db`. |
| `UPLIVION_ALLOWED_IP_RANGES` | Comma-separated CIDRs/IPs allowed to reach the private (admin/GUI) vhost, typically your LAN and/or VPN ranges. |
| `UPLIVION_PUBLIC_ORIGIN` | Base URL used to build share links, e.g. `https://share.example.com`. |
| `UPLIVION_PUBLIC_DOMAIN` | Hostname for the public vhost that serves only `/share`. |
| `UPLIVION_LAN_DOMAIN` | Hostname for the private admin/GUI vhost. |
| `UPLIVION_CERT_DOMAIN` | Domain the Let's Encrypt certificate was issued for, used by the public vhost. |

## Tests suite

See `tests/README.md` for the regression suite (37 Python + 2 frontend
test files covering proxy identity, upload state, session handling, admin
panel, profile, link privacy, and more).

## License

GPLv3. See [LICENSE](LICENSE).
