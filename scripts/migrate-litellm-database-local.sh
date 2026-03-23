#!/usr/bin/env bash
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

DB_USERNAME=${DB_USERNAME:-litellm}
DB_PASSWORD=${DB_PASSWORD:-litellm}
DB_HOST=${DB_HOST:-localhost}
DB_PORT=${DB_PORT:-5432}
LITELLM_DB_NAME=${LITELLM_DB_NAME:-litellm}

set -eo pipefail

CONTAINER_NAME="litellm_postgres"

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Error: Container ${CONTAINER_NAME} is not running."
    echo "Please start it with: docker compose -f litellm_docker_compose.yaml up -d"
    exit 1
fi

echo "[mlpa-litellm-migrate-local] Using database configuration:"
echo "  Container: ${CONTAINER_NAME}"
echo "  Username: ${DB_USERNAME}"
echo "  Database: ${LITELLM_DB_NAME}"
echo "  Host: ${DB_HOST}"
echo "  Port: ${DB_PORT}"

echo "[mlpa-litellm-migrate-local] Checking if database ${LITELLM_DB_NAME} exists..."
DB_EXISTS=$(docker exec "${CONTAINER_NAME}" psql -U "${DB_USERNAME}" -tAc "SELECT 1 FROM pg_database WHERE datname='${LITELLM_DB_NAME}';")

if [ "$DB_EXISTS" != "1" ]; then
    echo "[mlpa-litellm-migrate-local] Creating database ${LITELLM_DB_NAME}..."
    docker exec "${CONTAINER_NAME}" psql -U "${DB_USERNAME}" -c "CREATE DATABASE \"${LITELLM_DB_NAME}\";"
    echo "✅ Database ${LITELLM_DB_NAME} created successfully"
else
    echo "✅ Database ${LITELLM_DB_NAME} already exists"
fi

LITELLM_DATABASE_URL="postgresql://${DB_USERNAME}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${LITELLM_DB_NAME}"

echo ""
echo "[mlpa-litellm-migrate-local] Target (password redacted): postgresql://${DB_USERNAME}:***@${DB_HOST}:${DB_PORT}/${LITELLM_DB_NAME}"

echo "[mlpa-litellm-migrate-local] Running Alembic upgrade head (Alembic messages follow)..."
uv run alembic --raiseerr -c alembic_litellm.ini -x sqlalchemy.url="${LITELLM_DATABASE_URL}" upgrade head 2>&1

echo "✅ LiteLLM DB migrations completed successfully"
