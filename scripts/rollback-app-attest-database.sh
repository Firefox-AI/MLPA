#!/usr/bin/env bash
# Rolls the app_attest DB schema back via Alembic downgrade.
#
# migrate-app-attest-database.sh only moves forward (upgrade head) and
# aborts on any error. This is the actual recovery path when a deploy with
# a migration needs to be undone. Run it after the code rollback (git revert
# + Argo sync) is already done.
#
# Usage:
#   TARGET=-1 ./scripts/rollback-app-attest-database.sh           # step back one revision
#   TARGET=482f016f00d7 ./scripts/rollback-app-attest-database.sh # downgrade to a specific revision
#
# See docs/database-management.md#rollback-procedure before running against prod.
#
# Confirmation: several downgrade() functions in this repo do a real
# DROP TABLE, so this refuses to run unless CONFIRM=yes is set (non-interactive,
# e.g. the mlpa-rollback Job) or you answer the interactive prompt (a TTY).
set -euo pipefail

: "${DB_HOST:?DB_HOST is required}"
: "${DB_PORT:?DB_PORT is required}"
: "${DB_USERNAME:?DB_USERNAME is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required}"
: "${APP_ATTEST_DB_NAME:?APP_ATTEST_DB_NAME is required}"
TARGET="${TARGET:--1}"

if [ "${CONFIRM:-}" != "yes" ]; then
  if [ -t 0 ]; then
    read -r -p "This will run 'alembic downgrade ${TARGET}' against ${APP_ATTEST_DB_NAME} on ${DB_HOST}. Some migrations DROP TABLE. Type 'yes' to continue: " reply
    if [ "${reply}" != "yes" ]; then
      echo "[mlpa-appattest-rollback] Aborted." >&2
      exit 1
    fi
  else
    echo "[mlpa-appattest-rollback] Refusing to run non-interactively without CONFIRM=yes." >&2
    exit 1
  fi
fi

export PGHOST="${DB_HOST}"
export PGPORT="${DB_PORT}"
export PGUSER="${DB_USERNAME}"
export PGPASSWORD="${DB_PASSWORD}"
# URL-encode in case username/password contain characters that are reserved
# in a postgresql:// URL (@, /, :, %, +, all plausible in a generated secret).
DB_USERNAME_ENC="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "${DB_USERNAME}")"
DB_PASSWORD_ENC="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "${DB_PASSWORD}")"
APP_ATTEST_DATABASE_URL="postgresql://${DB_USERNAME_ENC}:${DB_PASSWORD_ENC}@${DB_HOST}:${DB_PORT}/${APP_ATTEST_DB_NAME}"

# mlpa-migrations sets a role-level statement_timeout/idle_in_transaction
# timeout (3s/10s in every environment) for normal upgrades, which are cheap
# CREATE TABLE/ADD COLUMN. A downgrade can do a real ALTER/DROP on a table
# that's grown large (challenges, public_keys), so this session gets no
# statement_timeout instead of inheriting the role default. lock_timeout
# stays bounded on its own: without it, a downgrade waiting on a lock held by
# app traffic or autovacuum would hang forever instead of failing fast, since
# there'd be no statement_timeout left to count the wait against.
export PGOPTIONS="-c statement_timeout=0 -c lock_timeout=30000 -c idle_in_transaction_session_timeout=0"

echo "[mlpa-appattest-rollback] Starting (host=${DB_HOST} port=${DB_PORT} database=${APP_ATTEST_DB_NAME} user=${DB_USERNAME} target=${TARGET})"

echo "[mlpa-appattest-rollback] Current revision before downgrade:"
alembic -c alembic.ini -x sqlalchemy.url="${APP_ATTEST_DATABASE_URL}" current

echo "[mlpa-appattest-rollback] Running Alembic downgrade to ${TARGET} (Alembic messages follow)..."
alembic --raiseerr -c alembic.ini -x sqlalchemy.url="${APP_ATTEST_DATABASE_URL}" downgrade "${TARGET}" 2>&1

echo "[mlpa-appattest-rollback] Revision after downgrade:"
alembic -c alembic.ini -x sqlalchemy.url="${APP_ATTEST_DATABASE_URL}" current

echo "[mlpa-appattest-rollback] Finished successfully."
