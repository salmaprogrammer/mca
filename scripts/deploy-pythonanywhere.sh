#!/usr/bin/env bash
#
# Deploy on PythonAnywhere.
#
# What this does, in order:
#   1. git pull to get the latest code
#   2. install any new dependencies
#   3. compile Arabic translations (.mo files are gitignored — skipping this
#      silently reverts every Arabic screen to English)
#   4. run migrations (safe when there are none)
#   5. reload the web app so the new code starts serving traffic
#
# Assumes the PythonAnywhere free/paid layout: code checked out under
# ~/mca and served via /var/www/<username>_pythonanywhere_com_wsgi.py.
# Change the two variables below if your setup differs.

set -Eeuo pipefail

APP_DIR="${APP_DIR:-$HOME/mca}"
VENV="${VENV:-$APP_DIR/.venv-flask}"
PA_USER="${PA_USER:-$(whoami)}"
WSGI_FILE="${WSGI_FILE:-/var/www/${PA_USER}_pythonanywhere_com_wsgi.py}"

cd "$APP_DIR"
export FLASK_APP=wsgi.py

echo "==> Recording current release for possible rollback"
git rev-parse HEAD > "$APP_DIR/.last-release"

echo "==> Fetching latest code"
git pull --ff-only

echo "==> Installing/updating dependencies"
"$VENV/bin/pip" install --quiet --upgrade -r requirements.txt

echo "==> Compiling Arabic translations"
"$VENV/bin/pybabel" compile -d app/translations

echo "==> Applying database migrations (no-op if none)"
"$VENV/bin/flask" db upgrade

echo "==> Reloading the web app"
if [ -f "$WSGI_FILE" ]; then
  touch "$WSGI_FILE"
  echo "    touched $WSGI_FILE — reload takes 5-10 seconds"
else
  echo "!!  WSGI file not found: $WSGI_FILE" >&2
  echo "    Set WSGI_FILE=/var/www/<your-file>.py and re-run, or reload from the Web tab." >&2
  exit 1
fi

echo "Deploy complete: $(git rev-parse --short HEAD)"
