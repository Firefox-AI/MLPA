PYTHON_VERSION=3.12
VENV=.venv

.PHONY: all setup install lint test run clean

all: setup

# dummy PR test
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
