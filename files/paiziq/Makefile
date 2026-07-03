# Paiziq automation commands.
# Run `make help` for a description of every target.

SDK_DIR     := sdk
INGEST_DIR  := services/ingest
VENV        := $(SDK_DIR)/.venv
PY          := $(VENV)/bin/python
PIP         := $(PY) -m pip

.DEFAULT_GOAL := help

.PHONY: help venv install lint typecheck test coverage examples build \
        openapi ingest-install ingest-test ingest-run check clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

venv: ## Create the SDK virtualenv (python3 -m venv)
	python3 -m venv $(VENV)

install: ## Install the SDK in editable mode with dev extras
	$(PIP) install -e "$(SDK_DIR)[dev]"

lint: ## Run ruff over SDK sources and tests
	$(PY) -m ruff check $(SDK_DIR)/src $(SDK_DIR)/tests

typecheck: ## Run mypy over the SDK package
	cd $(SDK_DIR) && .venv/bin/python -m mypy src/paiziq

test: ## Run the SDK test suite
	cd $(SDK_DIR) && .venv/bin/python -m pytest tests/ -q

coverage: ## Run SDK tests with coverage report
	cd $(SDK_DIR) && .venv/bin/python -m pytest tests/ -q \
		--cov=paiziq --cov-report=term-missing

examples: ## Run the runnable examples end to end
	cd $(SDK_DIR) && PYTHONPATH=src .venv/bin/python examples/happy_path.py
	cd $(SDK_DIR) && PYTHONPATH=src .venv/bin/python examples/framework_integrations.py

build: ## Build the sdist and wheel
	cd $(SDK_DIR) && .venv/bin/python -m build

openapi: ## Regenerate the OpenAPI spec and generated SDK client types
	cd $(INGEST_DIR) && ../../$(VENV)/bin/python scripts/export_openapi.py

ingest-install: ## Install ingest service dependencies
	$(PIP) install -r $(INGEST_DIR)/requirements.txt

ingest-test: ## Run the ingest service tests
	cd $(INGEST_DIR) && ../../$(VENV)/bin/python -m pytest tests/ -q

ingest-run: ## Run the ingest service locally on port 8800
	cd $(INGEST_DIR) && ../../$(VENV)/bin/python -m uvicorn app:app --reload --port 8800

check: lint test ingest-test examples ## Full quality gate (lint + all tests + examples)
	@echo "All checks passed."

clean: ## Remove build artifacts and caches
	rm -rf $(SDK_DIR)/dist $(SDK_DIR)/build $(SDK_DIR)/src/*.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} \;
