#!/usr/bin/env bash
set -euo pipefail

: "${DB_HOST:?DB_HOST is required}"
: "${DB_PORT:?DB_PORT is required}"
: "${DB_USERNAME:?DB_USERNAME is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required}"
: "${APP_ATTEST_DB_NAME:?APP_ATTEST_DB_NAME is required}"

export PGHOST="${DB_HOST}"
export PGPORT="${DB_PORT}"
export PGUSER="${DB_USERNAME}"
export PGPASSWORD="${DB_PASSWORD}"
APP_ATTEST_DATABASE_URL="postgresql://${DB_USERNAME}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${APP_ATTEST_DB_NAME}"

echo "[mlpa-appattest-migrate] Starting (host=${DB_HOST} port=${DB_PORT} database=${APP_ATTEST_DB_NAME} user=${DB_USERNAME})"
echo "[mlpa-appattest-migrate] Alembic config: alembic_appattest.ini"

echo "[mlpa-appattest-migrate] Checking if database exists..."
if psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${APP_ATTEST_DB_NAME}'" | grep -qx 1; then
  echo "[mlpa-appattest-migrate] Database '${APP_ATTEST_DB_NAME}' already exists."
else
  echo "[mlpa-appattest-migrate] Creating database '${APP_ATTEST_DB_NAME}'..."
  psql -d postgres -c "CREATE DATABASE \"${APP_ATTEST_DB_NAME}\";"
  echo "[mlpa-appattest-migrate] Database created."
fi

echo "[mlpa-appattest-migrate] Running Alembic upgrade head (Alembic messages follow)..."
alembic --raiseerr -c alembic_appattest.ini -x sqlalchemy.url="${APP_ATTEST_DATABASE_URL}" upgrade head 2>&1

echo "[mlpa-appattest-migrate] Finished successfully."
