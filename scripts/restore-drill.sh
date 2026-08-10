#!/usr/bin/env bash
#
# Restore drill (sprint S9.5).
#
# Restores the newest backup into a scratch database and checks the data is
# actually there, then drops the scratch database. This is the half of "we have
# backups" that usually goes untested until the day it matters.
#
# Run it monthly, and after any change to the schema or the backup script:
#   /srv/mca/scripts/restore-drill.sh
#
set -Eeuo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/mca}"
SCRATCH_DB="${SCRATCH_DB:-mca_restore_drill}"

if [[ -z "${PGHOST:-}${PGHOSTADDR:-}" && -z "${DATABASE_URL:-}" ]]; then
  echo "Set DATABASE_URL (or PG* vars) so the drill knows which server to use." >&2
  exit 1
fi

LATEST="$(find "$BACKUP_DIR" -name 'mca-*.dump' -type f | sort | tail -1)"
if [[ -z "$LATEST" ]]; then
  echo "No backups found in $BACKUP_DIR" >&2
  exit 1
fi

echo "Restoring $LATEST into $SCRATCH_DB"

cleanup() {
  dropdb --if-exists "$SCRATCH_DB" || true
}
trap cleanup EXIT

dropdb --if-exists "$SCRATCH_DB"
createdb "$SCRATCH_DB"
pg_restore --no-owner --no-privileges --dbname="$SCRATCH_DB" "$LATEST"

echo
echo "Row counts in the restored copy:"
psql --dbname="$SCRATCH_DB" --tuples-only --no-align --command "
  SELECT 'users:        ' || count(*) FROM users
  UNION ALL SELECT 'courses:      ' || count(*) FROM courses
  UNION ALL SELECT 'sessions:     ' || count(*) FROM sessions
  UNION ALL SELECT 'attendance:   ' || count(*) FROM attendance_records
  UNION ALL SELECT 'enrollments:  ' || count(*) FROM enrollments
  UNION ALL SELECT 'audit_log:    ' || count(*) FROM audit_log;
"

# A restore that produces an empty users table has "succeeded" and is useless.
USERS=$(psql --dbname="$SCRATCH_DB" --tuples-only --no-align \
        --command "SELECT count(*) FROM users;")
if (( USERS < 1 )); then
  echo "FAILED: restored database has no users." >&2
  exit 1
fi

# The schema must match what the app expects, or the restore is only half a
# recovery — you would be down until someone worked out the migration state.
CURRENT=$(psql --dbname="$SCRATCH_DB" --tuples-only --no-align \
          --command "SELECT version_num FROM alembic_version;")
echo
echo "Restored schema is at migration: $CURRENT"
echo "Compare with: FLASK_APP=wsgi.py flask db current"
echo
echo "Drill passed."
