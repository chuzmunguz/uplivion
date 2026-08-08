#!/usr/bin/env bash
set -euo pipefail

sudo -u uplivion /var/www/uplivion/venv/bin/python3 /var/www/uplivion/create_users.py
