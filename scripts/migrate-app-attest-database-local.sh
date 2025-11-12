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
APP_ATTEST_DB_NAME=${APP_ATTEST_DB_NAME:-app_attest}

# Docker container name
CONTAINER_NAME="litellm_postgres"

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Error: Container ${CONTAINER_NAME} is not running."
    echo "Please start it with: docker compose -f litellm_docker_compose.yaml up -d"
    exit 1
fi

echo "Using database configuration:"
echo "  Container: ${CONTAINER_NAME}"
echo "  Username: ${DB_USERNAME}"
echo "  Database: ${APP_ATTEST_DB_NAME}"
echo "  Host: ${DB_HOST}"
echo "  Port: ${DB_PORT}"

# Create the app_attest database if it doesn't exist
echo "Checking if database ${APP_ATTEST_DB_NAME} exists..."
DB_EXISTS=$(docker exec ${CONTAINER_NAME} psql -U ${DB_USERNAME} -tAc "SELECT 1 FROM pg_database WHERE datname='${APP_ATTEST_DB_NAME}';")

if [ "$DB_EXISTS" != "1" ]; then
    echo "Creating database ${APP_ATTEST_DB_NAME}..."
    docker exec ${CONTAINER_NAME} psql -U ${DB_USERNAME} -c "CREATE DATABASE \"${APP_ATTEST_DB_NAME}\";"
    if [ $? -eq 0 ]; then
        echo "✅ Database ${APP_ATTEST_DB_NAME} created successfully"
    else
        echo "❌ Failed to create database ${APP_ATTEST_DB_NAME}"
        exit 1
    fi
else
    echo "✅ Database ${APP_ATTEST_DB_NAME} already exists"
fi

# Run alembic migrations
APP_ATTEST_DATABASE_URL="postgresql://${DB_USERNAME}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${APP_ATTEST_DB_NAME}"
echo ""
echo "Running Alembic migrations..."
echo "Using APP_ATTEST_DATABASE_URL: postgresql://${DB_USERNAME}:***@${DB_HOST}:${DB_PORT}/${APP_ATTEST_DB_NAME}"

alembic -x sqlalchemy.url="${APP_ATTEST_DATABASE_URL}" upgrade head

if [ $? -eq 0 ]; then
    echo "✅ Migrations completed successfully"
else
    echo "❌ Migrations failed"
    exit 1
fi
