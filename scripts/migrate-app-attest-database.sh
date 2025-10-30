APP_ATTEST_DATABASE_URL="postgresql://${DB_USERNAME}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${APP_ATTEST_DB_NAME}"
alembic -x sqlalchemy.url="${APP_ATTEST_DATABASE_URL}" upgrade head
