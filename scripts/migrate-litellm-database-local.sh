#!/bin/bash

# Load environment variables from .env file if it exists
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Set defaults from docker-compose.yaml if not set in .env
DB_USERNAME=${DB_USERNAME:-litellm}
DB_PASSWORD=${DB_PASSWORD:-litellm}
DB_HOST=${DB_HOST:-localhost}
DB_PORT=${DB_PORT:-5432}
LITELLM_DB_NAME=${LITELLM_DB_NAME:-litellm}

CONTAINER_NAME="litellm_postgres"

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Error: Container ${CONTAINER_NAME} is not running."
    echo "Please start it with: docker compose -f litellm_docker_compose.yaml up -d"
    exit 1
fi

echo "Using database configuration:"
echo "  Container: ${CONTAINER_NAME}"
echo "  Username: ${DB_USERNAME}"
echo "  Database: ${LITELLM_DB_NAME}"
echo "  Host: ${DB_HOST}"
echo "  Port: ${DB_PORT}"

echo "Checking if database ${LITELLM_DB_NAME} exists..."
DB_EXISTS=$(docker exec ${CONTAINER_NAME} psql -U ${DB_USERNAME} -tAc "SELECT 1 FROM pg_database WHERE datname='${LITELLM_DB_NAME}';")

if [ "$DB_EXISTS" != "1" ]; then
    echo "Creating database ${LITELLM_DB_NAME}..."
    docker exec ${CONTAINER_NAME} psql -U ${DB_USERNAME} -c "CREATE DATABASE \"${LITELLM_DB_NAME}\";"
    if [ $? -ne 0 ]; then
        echo "❌ Failed to create database ${LITELLM_DB_NAME}"
        exit 1
    fi
    echo "✅ Database ${LITELLM_DB_NAME} created successfully"
else
    echo "✅ Database ${LITELLM_DB_NAME} already exists"
fi

LITELLM_DATABASE_URL="postgresql://${DB_USERNAME}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${LITELLM_DB_NAME}"
echo ""
echo "Running Alembic migrations (LiteLLM DB / MLPA tables)..."
echo "Using LITELLM_DATABASE_URL: postgresql://${DB_USERNAME}:***@${DB_HOST}:${DB_PORT}/${LITELLM_DB_NAME}"

alembic -c alembic_litellm.ini -x sqlalchemy.url="${LITELLM_DATABASE_URL}" upgrade head

if [ $? -eq 0 ]; then
    echo "✅ LiteLLM DB migrations completed successfully"
else
    echo "❌ LiteLLM DB migrations failed"
    exit 1
fi
