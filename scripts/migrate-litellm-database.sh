#!/usr/bin/env bash
set -euo pipefail

: "${DB_HOST:?DB_HOST is required}"
: "${DB_PORT:?DB_PORT is required}"
: "${DB_USERNAME:?DB_USERNAME is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required}"
: "${LiteLLM_DB_NAME:?LiteLLM_DB_NAME is required}"

ACTION="${1:-migrate}"

export PGHOST="${DB_HOST}"
export PGPORT="${DB_PORT}"
export PGUSER="${DB_USERNAME}"
export PGPASSWORD="${DB_PASSWORD}"

echo "[mlpa-litellm-migrate] Starting action=${ACTION} host=${DB_HOST} port=${DB_PORT} database=${LiteLLM_DB_NAME} user=${DB_USERNAME}"

if ! psql -d "${LiteLLM_DB_NAME}" -tAc "SELECT to_regclass('public.\"LiteLLM_SpendLogs\"') IS NOT NULL" | grep -qx t; then
  echo "[mlpa-litellm-migrate] LiteLLM_SpendLogs does not exist yet. Skipping."
  exit 0
fi

psql -v ON_ERROR_STOP=1 -d "${LiteLLM_DB_NAME}" <<'SQL'
CREATE MATERIALIZED VIEW IF NOT EXISTS public.monthly_global_spend_cache AS
SELECT
  1 AS cache_key,
  COALESCE(SUM(spend), 0) AS spend
FROM public."LiteLLM_SpendLogs"
WHERE "startTime" >= NOW() - INTERVAL '30 days';

CREATE UNIQUE INDEX IF NOT EXISTS monthly_global_spend_cache_cache_key_idx
  ON public.monthly_global_spend_cache (cache_key);

DROP VIEW IF EXISTS public."MonthlyGlobalSpend";

CREATE VIEW public."MonthlyGlobalSpend" AS
SELECT spend
FROM public.monthly_global_spend_cache;
SQL

if [[ "${ACTION}" == "refresh" ]]; then
  echo "[mlpa-litellm-migrate] Refreshing materialized view concurrently."
  psql -v ON_ERROR_STOP=1 -d "${LiteLLM_DB_NAME}" -c \
    'REFRESH MATERIALIZED VIEW CONCURRENTLY public.monthly_global_spend_cache;'
fi

echo "[mlpa-litellm-migrate] Finished successfully."
