#!/usr/bin/env bash
#
# Nightly database backup (sprint S9.5).
#
# Writes a compressed dump, prunes old ones, and — the part people skip —
# verifies the dump is restorable before deleting anything. A backup nobody
# has restored is not a backup, it is a file.
#
# Cron (as the app user):
#   15 2 * * *  /srv/mca/scripts/backup.sh >> /var/log/mca-backup.log 2>&1
#
set -Eeuo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/mca}"
RETAIN_DAYS="${RETAIN_DAYS:-30}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is not set; refusing to run." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
DUMP="$BACKUP_DIR/mca-$STAMP.dump"

echo "[$(date -u +%FT%TZ)] dumping to $DUMP"
# Custom format: compressed, and restorable selectively with pg_restore.
pg_dump --format=custom --no-owner --no-privileges --dbname="$DATABASE_URL" --file="$DUMP"

SIZE=$(wc -c < "$DUMP")
if (( SIZE < 1024 )); then
  echo "Dump is only ${SIZE} bytes — treating as failed." >&2
  exit 1
fi

# Verify by listing the archive's table of contents. A truncated or corrupt
# dump fails here rather than on the night you actually need it.
echo "[$(date -u +%FT%TZ)] verifying archive"
pg_restore --list "$DUMP" > /dev/null

echo "[$(date -u +%FT%TZ)] ok: $DUMP ($SIZE bytes)"

# Only prune once today's dump is known good.
find "$BACKUP_DIR" -name 'mca-*.dump' -type f -mtime "+$RETAIN_DAYS" -print -delete

if [[ -n "${BACKUP_REMOTE:-}" ]]; then
  echo "[$(date -u +%FT%TZ)] copying off-box to $BACKUP_REMOTE"
  # A backup on the same disk as the database survives nothing that matters.
  rsync --archive --quiet "$DUMP" "$BACKUP_REMOTE/"
else
  echo "WARNING: BACKUP_REMOTE is unset — this backup is on the same host as the database." >&2
fi
