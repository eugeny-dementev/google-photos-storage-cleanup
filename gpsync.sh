#!/usr/bin/env bash
# Run gpsync with this project's .venv. No need to activate it.
set -eu
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" -m gpsync "$@"
fi
if [[ -f "$ROOT/.venv/Scripts/python.exe" ]]; then
  exec "$ROOT/.venv/Scripts/python.exe" -m gpsync "$@"
fi

echo "No project .venv found. From this folder, once:" >&2
echo "  python3 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/playwright install chromium" >&2
exit 1
