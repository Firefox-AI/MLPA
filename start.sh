#!/usr/bin/env bash
set -euo pipefail

docker compose -f litellm_docker_compose.yaml down --volumes --remove-orphans
docker compose -f litellm_docker_compose.yaml up -d
bash scripts/migrate-app-attest-database-local.sh

until curl -fsS http://localhost:4000/health/readiness >/dev/null; do
    echo "Waiting for litellm..."
    sleep 5
done

uv run python scripts/create-and-set-virtual-key.py
mlpa
