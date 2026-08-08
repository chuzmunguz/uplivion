#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UPLIVION_TEST_PYTHON="${UPLIVION_TEST_PYTHON:-python3}"

cd "$ROOT"
export PYTHONDONTWRITEBYTECODE=1
exec "$UPLIVION_TEST_PYTHON" -m unittest discover -s tests -p 'test_*.py'
