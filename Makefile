PYTHON_VERSION=3.12
VENV=.venv

.PHONY: all setup install lint test run clean docs

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

clean:
	rm -rf __pycache__ .cache $(VENV)

docs:
	uv run python -c "from mlpa.run import app; import json; json.dump(app.openapi(), open('openapi.json', 'w'), indent=2)" && \
	npx --yes @redocly/cli build-docs openapi.json -o docs/index.html && \
	rm -f openapi.json
