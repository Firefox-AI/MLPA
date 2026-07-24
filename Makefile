PYTHON_VERSION=3.12
VENV=.venv

.PHONY: all setup node-setup install lint test run clean docs fxa-user-id docker-up docker-down

all: setup

setup: node-setup
	uv venv --python $(PYTHON_VERSION)
	uv sync --all-groups
	uv run pre-commit install
	@echo ""
	@echo "✅ Setup complete! To activate your environment, run:"
	@echo "   source $(VENV)/bin/activate"

# Ensure the Node version `make docs` needs (.nvmrc). Best-effort and non-fatal.
# `styled-components` (pinned in `docs`) generates the CSS hashes rather than Node.
node-setup:
	@want=$$(cat .nvmrc); \
	if command -v node >/dev/null 2>&1 && [ "$$(node -v | sed 's/v\([0-9]*\).*/\1/')" = "$$want" ]; then \
		echo "✅ Node $$want already active."; \
	elif [ -s "$$NVM_DIR/nvm.sh" ]; then \
		. "$$NVM_DIR/nvm.sh" && nvm install && echo "✅ Installed Node $$want via nvm ('nvm use' to activate)."; \
	else \
		echo "⚠️  Node $$want not active and nvm not found (needed for 'make docs')."; \
		echo "   Install it, e.g.: brew install node@$$want  (then add it to PATH), or use nvm."; \
	fi

install:
	uv pip install --no-cache-dir -e .

lint:
	uv run ruff check .
	uv run ty check

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
	@if [ -s "$$NVM_DIR/nvm.sh" ]; then . "$$NVM_DIR/nvm.sh" && nvm use >/dev/null; fi; \
	want=$$(cat .nvmrc); have=$$(node -v | sed 's/v\([0-9]*\).*/\1/'); \
	if [ "$$have" != "$$want" ]; then \
		echo "make docs needs Node $$want (see .nvmrc); found Node $$have."; \
		echo "Switch with: nvm use  (or e.g. brew install node@$$want)"; \
		exit 1; \
	fi; \
	uv run python -c "from mlpa.run import app; import json; json.dump(app.openapi(), open('openapi.json', 'w'), indent=2)" && \
	npx --yes -p @redocly/cli@2.5.0 -p styled-components@6.4.3 redocly build-docs openapi.json -o docs/index.html && \
	rm -f openapi.json

# NOTE: for local development only
docker-up:
	docker-compose -f litellm_docker_compose.yaml up -d
	bash scripts/migrate-app-attest-database-local.sh
	uv run python scripts/create-and-set-virtual-key.py

docker-down:
	docker-compose -f litellm_docker_compose.yaml down -v
