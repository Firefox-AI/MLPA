#!/usr/bin/env bash
set -euo pipefail

: "${DB_HOST:?DB_HOST is required}"
: "${DB_PORT:?DB_PORT is required}"
: "${DB_USERNAME:?DB_USERNAME is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required}"
: "${LITELLM_DB_NAME:?LITELLM_DB_NAME is required}"

export PGHOST="${DB_HOST}"
export PGPORT="${DB_PORT}"
export PGUSER="${DB_USERNAME}"
export PGPASSWORD="${DB_PASSWORD}"
# Used only for Alembic (never echoed).
LITELLM_DATABASE_URL="postgresql://${DB_USERNAME}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${LITELLM_DB_NAME}"

echo "[mlpa-litellm-migrate] Starting (host=${DB_HOST} port=${DB_PORT} database=${LITELLM_DB_NAME} user=${DB_USERNAME})"
echo "[mlpa-litellm-migrate] Alembic config: alembic_litellm.ini (revision chain under alembic_litellm/)"

echo "[mlpa-litellm-migrate] Checking if database exists..."
if psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${LITELLM_DB_NAME}'" | grep -qx 1; then
  echo "[mlpa-litellm-migrate] Database '${LITELLM_DB_NAME}' already exists."
else
  echo "[mlpa-litellm-migrate] Creating database '${LITELLM_DB_NAME}'..."
  psql -d postgres -c "CREATE DATABASE \"${LITELLM_DB_NAME}\";"
  echo "[mlpa-litellm-migrate] Database created."
fi

# Alembic logs to stderr; merge to stdout so platforms that only ingest stdout show migration output.
echo "[mlpa-litellm-migrate] Running Alembic upgrade head (Alembic messages follow)..."
alembic --raiseerr -c alembic_litellm.ini -x sqlalchemy.url="${LITELLM_DATABASE_URL}" upgrade head 2>&1

echo "[mlpa-litellm-migrate] Finished successfully."
