#!/usr/bin/env bash
set -euo pipefail

: "${DB_HOST:?DB_HOST is required}"
: "${DB_PORT:?DB_PORT is required}"
: "${DB_USERNAME:?DB_USERNAME is required}"
: "${DB_PASSWORD:?DB_PASSWORD is required}"
: "${LiteLLM_DB_NAME:?LiteLLM_DB_NAME is required}"

ACTION="${1:-migrate}"

case "${ACTION}" in
  migrate|refresh) ;;
  *)
    echo "Unknown action: ${ACTION}"
    exit 2
    ;;
esac

export PGHOST="${DB_HOST}"
export PGPORT="${DB_PORT}"
export PGUSER="${DB_USERNAME}"
export PGPASSWORD="${DB_PASSWORD}"

query_bool() {
  local sql="$1"
  local result

  result="$(
    psql -v ON_ERROR_STOP=1 -d "${LiteLLM_DB_NAME}" -tAc "${sql}"
  )"
  echo "${result}" | tr -d '[:space:]'
}

echo "[mlpa-litellm-migrate] Starting action=${ACTION} host=${DB_HOST} port=${DB_PORT} database=${LiteLLM_DB_NAME} user=${DB_USERNAME}"

if [[ "${ACTION}" == "refresh" ]]; then
  if [[ "$(query_bool "SELECT to_regclass('public.monthly_global_spend_cache') IS NOT NULL")" != "t" ]]; then
    echo "[mlpa-litellm-migrate] Cache missing; run migrate first. Skipping."
    exit 0
  fi

  echo "[mlpa-litellm-migrate] Refreshing materialized view concurrently."
  psql -v ON_ERROR_STOP=1 -d "${LiteLLM_DB_NAME}" -c \
    'REFRESH MATERIALIZED VIEW CONCURRENTLY public.monthly_global_spend_cache;'
  echo "[mlpa-litellm-migrate] Finished successfully."
  exit 0
fi

if [[ "$(query_bool "SELECT to_regclass('public.\"LiteLLM_SpendLogs\"') IS NOT NULL")" != "t" ]]; then
  echo "[mlpa-litellm-migrate] LiteLLM_SpendLogs does not exist yet. Skipping."
  exit 0
fi

psql -v ON_ERROR_STOP=1 -d "${LiteLLM_DB_NAME}" <<'SQL'
-- NOTE: Postgres has no CREATE OR REPLACE MATERIALIZED VIEW. To change the
-- SELECT below, add a one-shot migration step that runs
--   DROP MATERIALIZED VIEW IF EXISTS public.monthly_global_spend_cache;
-- before this block, otherwise the new definition will be ignored.
CREATE MATERIALIZED VIEW IF NOT EXISTS public.monthly_global_spend_cache AS
SELECT
  DATE("startTime") AS date,
  COALESCE(SUM(spend), 0) AS spend
FROM public."LiteLLM_SpendLogs"
WHERE "startTime" >= (CURRENT_DATE - INTERVAL '30 days')
GROUP BY DATE("startTime");

CREATE UNIQUE INDEX IF NOT EXISTS monthly_global_spend_cache_date_idx
  ON public.monthly_global_spend_cache (date);

CREATE OR REPLACE VIEW public."MonthlyGlobalSpend" AS
SELECT date, spend
FROM public.monthly_global_spend_cache;
SQL

echo "[mlpa-litellm-migrate] Finished successfully."
