#!/usr/bin/env bash
#
# Deploy (sprint S10.2).
#
# Ordering matters and is deliberate:
#   1. back up BEFORE migrating, so a bad migration is recoverable
#   2. install deps and compile translations BEFORE migrating
#   3. migrate, then preflight, then restart — preflight gates the restart
#
# Rollback: scripts/rollback.sh
#
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/srv/mca}"
VENV="${VENV:-$APP_DIR/.venv}"
SERVICE="${SERVICE:-mca}"
cd "$APP_DIR"

export FLASK_APP=wsgi.py
set -a; source "$APP_DIR/.env"; set +a

echo "==> Recording the current release for rollback"
git rev-parse HEAD > "$APP_DIR/.last-release"

echo "==> Backing up the database first"
"$APP_DIR/scripts/backup.sh"

echo "==> Fetching code"
git pull --ff-only

echo "==> Installing dependencies"
"$VENV/bin/pip" install --quiet --upgrade -r requirements.txt

echo "==> Compiling translations"
# .mo files are gitignored, so this is not optional: skipping it silently
# reverts every Arabic screen to English.
"$VENV/bin/pybabel" compile -d app/translations

echo "==> Applying migrations"
"$VENV/bin/flask" db upgrade

echo "==> Seeding fixed reference data (idempotent)"
"$VENV/bin/flask" seed-course-types
"$VENV/bin/flask" seed-terms

echo "==> Preflight"
# Non-zero here aborts the deploy before the new code ever serves a request.
"$VENV/bin/flask" preflight

echo "==> Restarting"
sudo systemctl restart "$SERVICE"

echo "==> Waiting for health"
for attempt in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:8000/healthz" > /dev/null 2>&1; then
    echo "    healthy after ${attempt}s"
    echo "Deploy complete: $(git rev-parse --short HEAD)"
    exit 0
  fi
  sleep 1
done

echo "Service did not become healthy. Roll back with scripts/rollback.sh" >&2
exit 1
