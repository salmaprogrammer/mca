#!/usr/bin/env bash
#
# Restore drill for the SQLite development database (sprint S9.5).
#
# The production drill is restore-drill.sh (Postgres). This is the same
# procedure against the dev database, so the *steps* can be exercised before
# there is a production server to exercise them on.
#
# Run: ./scripts/restore-drill-sqlite.sh
#
set -Eeuo pipefail

DB="${DB:-instance/mca-dev.sqlite}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

[[ -f "$DB" ]] || { echo "No database at $DB" >&2; exit 1; }

echo "1. Backing up $DB"
# `.backup` is consistent even while the app is running; copying the file is not.
sqlite3 "$DB" ".backup '$WORK/hot.sqlite'"
sqlite3 "$WORK/hot.sqlite" .dump > "$WORK/backup.sql"
echo "   dump: $(wc -c < "$WORK/backup.sql") bytes"

echo "2. Restoring into a scratch database"
sqlite3 "$WORK/restored.sqlite" < "$WORK/backup.sql"

echo "3. Verifying the restored copy"
for table in users courses sessions attendance_records enrollments audit_log; do
  count=$(sqlite3 "$WORK/restored.sqlite" "SELECT count(*) FROM $table;")
  printf "   %-20s %s\n" "$table:" "$count"
done

# "Restored successfully" with an empty users table is a failed recovery that
# reports success, which is worse than an obvious error.
users=$(sqlite3 "$WORK/restored.sqlite" "SELECT count(*) FROM users;")
(( users >= 1 )) || { echo "FAILED: no users in the restored copy." >&2; exit 1; }

echo "4. Schema version check"
source_version=$(sqlite3 "$DB" "SELECT version_num FROM alembic_version;")
restored_version=$(sqlite3 "$WORK/restored.sqlite" "SELECT version_num FROM alembic_version;")
echo "   source:   $source_version"
echo "   restored: $restored_version"
[[ "$source_version" == "$restored_version" ]] || {
  echo "FAILED: schema version mismatch." >&2
  exit 1
}

echo
echo "Drill passed — restored copy is complete and at the same migration."
