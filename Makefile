PYTHON_VERSION=3.12
VENV=.venv

.PHONY: all setup install lint test run clean docs fxa-user-id docker-up docker-down

all: setup

setup:
	uv venv --python $(PYTHON_VERSION)
	uv sync --all-groups
	uv run pre-commit install
	@echo ""
	@echo "✅ Setup complete! To activate your environment, run:"
	@echo "   source $(VENV)/bin/activate"

install:
	uv pip install --no-cache-dir -e .

lint:
	uv run ruff check .

test:
	uv run pytest -v

mlpa:
	$(VENV)/bin/mlpa

# Example:
# make fxa-user-id ARGS="--token YOUR_BEARER_TOKEN"
# make fxa-user-id ARGS="--email you@example.com --password your_password"
fxa-user-id:
	uv run python scripts/fxa_user_id.py $(ARGS)

clean:
	rm -rf __pycache__ .cache $(VENV)

docs:
	uv run python -c "from mlpa.run import app; import json; json.dump(app.openapi(), open('openapi.json', 'w'), indent=2)" && \
	npx --yes @redocly/cli build-docs openapi.json -o docs/index.html && \
	rm -f openapi.json

# NOTE: for local development only
docker-up:
	docker-compose -f litellm_docker_compose.yaml up -d
	bash scripts/migrate-app-attest-database-local.sh
	uv run python scripts/create-and-set-virtual-key.py

docker-down:
	docker-compose -f litellm_docker_compose.yaml down -v
