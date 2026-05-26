docker compose -f litellm_docker_compose.yaml down --volumes --remove-orphans
docker compose -f litellm_docker_compose.yaml up -d
sh ./scripts/create-app-attest-database.sh
sh scripts/migrate-app-attest-database-local.sh

until curl -s -o /dev/null -w "%{http_code}" http://localhost:4000/health/liveness | grep -q "200"; do
    echo "Waiting for litellm..."
    sleep 5
done

python scripts/create-and-set-virtual-key.py
