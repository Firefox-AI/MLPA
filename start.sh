#!/usr/bin/env bash
set -euo pipefail

if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

compose_profiles="${COMPOSE_PROFILES:-}"
privacy_filter_enabled="$(printf '%s' "${PRIVACY_FILTER_ENABLED:-false}" | tr '[:upper:]' '[:lower:]')"
case "${privacy_filter_enabled}" in
    true | 1 | yes | on | y | t)
        case ",${compose_profiles}," in
            *,privacy-filter,*) ;;
            *)
                compose_profiles="${compose_profiles:+${compose_profiles},}privacy-filter"
                ;;
        esac
        ;;
esac

COMPOSE_PROFILES="${compose_profiles}" docker compose \
    -f litellm_docker_compose.yaml \
    down --volumes --remove-orphans
COMPOSE_PROFILES="${compose_profiles}" docker compose \
    -f litellm_docker_compose.yaml \
    up -d

bash scripts/create-app-attest-database.sh
bash scripts/migrate-app-attest-database-local.sh

until curl -fsS http://localhost:4000/health/readiness >/dev/null; do
    echo "Waiting for litellm..."
    sleep 5
done

uv run python scripts/create-and-set-virtual-key.py
mlpa
