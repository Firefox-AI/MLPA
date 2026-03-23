#!/bin/bash

LITELLM_DATABASE_URL="postgresql://${DB_USERNAME}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${LITELLM_DB_NAME}"
echo "Using LITELLM_DATABASE_URL: ${LITELLM_DATABASE_URL}"
# Create the LiteLLM database if it doesn't exist (LiteLLM may also create it)
psql "postgresql://${DB_USERNAME}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/postgres" \
    -c "SELECT 1 FROM pg_database WHERE datname='${LITELLM_DB_NAME}';" | grep -q 1 || \
    psql "postgresql://${DB_USERNAME}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/postgres" \
    -c "CREATE DATABASE \"${LITELLM_DB_NAME}\";"

alembic -c alembic_litellm.ini -x sqlalchemy.url="${LITELLM_DATABASE_URL}" upgrade head
