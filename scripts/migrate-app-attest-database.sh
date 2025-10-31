#!/bin/bash

APP_ATTEST_DATABASE_URL="postgresql://${DB_USERNAME}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${APP_ATTEST_DB_NAME}"
echo "Using APP_ATTEST_DATABASE_URL: ${APP_ATTEST_DATABASE_URL}"
# Create the app_attest database if it doesn't exist
psql "postgresql://${DB_USERNAME}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/postgres" \
    -c "SELECT 1 FROM pg_database WHERE datname='${APP_ATTEST_DB_NAME}';" | grep -q 1 || \
    psql "postgresql://${DB_USERNAME}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/postgres" \
    -c "CREATE DATABASE \"${APP_ATTEST_DB_NAME}\";"

alembic -x sqlalchemy.url="${APP_ATTEST_DATABASE_URL}" upgrade head
