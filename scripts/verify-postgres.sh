#!/usr/bin/env bash
#
# Verify the app against a throwaway Postgres before it ever touches real data
# (sprint S10.1).
#
# Every migration in this project has only ever run on SQLite. The schema
# compiles cleanly for Postgres and avoids SQLite-only constructs, but the
# first real run is still the first real run — and doing it on an empty
# database costs nothing, while doing it on live data costs a restore.
#
# Run on the server once Postgres is installed, before creating the real
# database:
#
#   ./scripts/verify-postgres.sh
#
set -Eeuo pipefail

SCRATCH_DB="${SCRATCH_DB:-mca_verify}"
PGUSER_ROLE="${PGUSER_ROLE:-$(whoami)}"
VENV="${VENV:-.venv-flask}"
[[ -x "$VENV/bin/python" ]] || VENV=".venv"

export FLASK_APP=wsgi.py

cleanup() {
  echo "==> Dropping $SCRATCH_DB"
  dropdb --if-exists "$SCRATCH_DB" || true
}
trap cleanup EXIT

echo "==> Creating throwaway database $SCRATCH_DB"
dropdb --if-exists "$SCRATCH_DB"
createdb "$SCRATCH_DB"

URL="postgresql+psycopg://${PGUSER_ROLE}@localhost:5432/${SCRATCH_DB}"

echo "==> Running the full migration chain"
DATABASE_URL="$URL" FLASK_CONFIG=production \
  SECRET_KEY="verify-only-$(head -c 32 /dev/urandom | base64 | tr -d '/+=')" \
  "$VENV/bin/flask" db upgrade

echo "==> Checking the schema landed at head"
DATABASE_URL="$URL" FLASK_CONFIG=production \
  SECRET_KEY="verify-only-$(head -c 32 /dev/urandom | base64 | tr -d '/+=')" \
  "$VENV/bin/flask" db current

echo "==> Exercising downgrade and re-upgrade"
# Proves the migrations are reversible on Postgres too, which is what makes a
# bad deploy recoverable without a restore.
for _ in 1 2 3; do
  DATABASE_URL="$URL" FLASK_CONFIG=production \
    SECRET_KEY="verify-only-x$(date +%s)$(head -c 16 /dev/urandom | base64 | tr -d '/+=')" \
    "$VENV/bin/flask" db downgrade
done
DATABASE_URL="$URL" FLASK_CONFIG=production \
  SECRET_KEY="verify-only-y$(head -c 32 /dev/urandom | base64 | tr -d '/+=')" \
  "$VENV/bin/flask" db upgrade

echo "==> Running the whole test suite against Postgres"
# The suite normally uses in-memory SQLite; TEST_DATABASE_URL redirects it.
TEST_DATABASE_URL="$URL" "$VENV/bin/python" -m pytest -q

echo
echo "Postgres verification passed. Safe to create the real database."
