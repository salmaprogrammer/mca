#!/usr/bin/env bash
#
# Roll back to the previous release (sprint S10.2).
#
# Code only. Migrations are NOT reversed automatically: `flask db downgrade`
# drops columns and tables, and on a live database that destroys data that the
# older code simply would not have shown. If the new release added a migration
# and you must go back past it, restore from the pre-deploy backup instead —
# that is why deploy.sh takes one first.
#
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/srv/mca}"
VENV="${VENV:-$APP_DIR/.venv}"
SERVICE="${SERVICE:-mca}"
cd "$APP_DIR"

[[ -f .last-release ]] || { echo "No .last-release recorded." >&2; exit 1; }
TARGET="$(cat .last-release)"

export FLASK_APP=wsgi.py
set -a; source "$APP_DIR/.env"; set +a

CURRENT_HEAD="$("$VENV/bin/flask" db current 2>/dev/null | tail -1 || true)"

echo "==> Rolling back code to $TARGET"
git checkout --quiet "$TARGET"
"$VENV/bin/pip" install --quiet --upgrade -r requirements.txt
"$VENV/bin/pybabel" compile -d app/translations

echo "==> Restarting"
sudo systemctl restart "$SERVICE"

for attempt in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:8000/healthz" > /dev/null 2>&1; then
    echo "    healthy after ${attempt}s"
    echo
    echo "Rolled back to $TARGET."
    echo "Database is still at: $CURRENT_HEAD"
    echo "If the old code cannot run against that schema, restore the"
    echo "pre-deploy backup from /var/backups/mca — do not run 'db downgrade'"
    echo "against live data."
    exit 0
  fi
  sleep 1
done

echo "Rollback did not become healthy — investigate immediately." >&2
exit 1
