# Paiziq automation commands.
# Run `make help` for a description of every target.

SDK_DIR     := sdk
INGEST_DIR  := services/ingest
VENV        := $(SDK_DIR)/.venv
PY          := $(VENV)/bin/python
PIP         := $(PY) -m pip

.DEFAULT_GOAL := help

.PHONY: help venv install lint typecheck test coverage examples build \
        openapi ingest-install ingest-test ingest-run check clean \
        e2e-stack e2e-seed e2e-verify e2e-test \
        docker-build docker-smoke smoke northstar-demo deploy-azure

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | \
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
	cd $(SDK_DIR) && PYTHONPATH=src .venv/bin/python examples/payment_agent.py

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

E2E_DB ?= $(CURDIR)/.e2e/paiziq-e2e.sqlite
E2E_PORT ?= 8800
PAIZIQ_ENDPOINT ?= http://127.0.0.1:$(E2E_PORT)
PAIZIQ_API_KEY ?= dev-key
PAIZIQ_CORS_ORIGINS ?= http://127.0.0.1:4173

e2e-stack: ## Run ingest on :8800 with an isolated SQLite file and dashboard CORS
	mkdir -p "$(dir $(E2E_DB))"
	cd $(INGEST_DIR) && PAIZIQ_ENV=development PAIZIQ_INGEST_KEYS=dev-key \
		PAIZIQ_INGEST_DB="$(E2E_DB)" PAIZIQ_CORS_ORIGINS="$(PAIZIQ_CORS_ORIGINS)" \
		../../$(VENV)/bin/python -m uvicorn app:app --host 127.0.0.1 --port $(E2E_PORT)

e2e-seed: ## Create the deterministic agent, policy, and three payments
	cd $(INGEST_DIR) && PAIZIQ_ENDPOINT="$(PAIZIQ_ENDPOINT)" PAIZIQ_API_KEY="$(PAIZIQ_API_KEY)" \
		../../$(VENV)/bin/python tests/e2e_support/simulated_agent.py

e2e-verify: ## Exercise journey endpoints and write the results markdown
	cd $(INGEST_DIR) && PAIZIQ_ENDPOINT="$(PAIZIQ_ENDPOINT)" PAIZIQ_API_KEY="$(PAIZIQ_API_KEY)" \
		../../$(VENV)/bin/python scripts/e2e_verify_endpoints.py

e2e-test: ## Run the payment-agent workflow integration tests
	cd $(INGEST_DIR) && ../../$(VENV)/bin/python -m pytest tests/test_payment_agent_workflow_e2e.py -q

DOCKER_IMAGE ?= paiziq-ingest:local
DOCKER_SMOKE_PORT ?= 8801
PAIZIQ_DASHBOARD_URL ?=

docker-build: ## Build the backend container image (context: this directory)
	docker build -f $(INGEST_DIR)/Dockerfile -t $(DOCKER_IMAGE) .

docker-smoke: docker-build ## Start the image in production mode and run the smoke test against it
	@docker rm -f paiziq-ingest-smoke >/dev/null 2>&1 || true
	@KEY="pzq_smoke_$$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"; \
	docker run -d --rm --name paiziq-ingest-smoke -p $(DOCKER_SMOKE_PORT):8800 \
		-e PAIZIQ_ENV=production -e PAIZIQ_INGEST_KEYS="$$KEY" \
		-e PAIZIQ_CORS_ORIGINS="$(PAIZIQ_CORS_ORIGINS)" $(DOCKER_IMAGE) >/dev/null; \
	for i in $$(seq 1 30); do \
		curl -fsS http://127.0.0.1:$(DOCKER_SMOKE_PORT)/health >/dev/null 2>&1 && break; sleep 1; \
	done; \
	PAIZIQ_API_KEY="$$KEY" python3 $(INGEST_DIR)/scripts/smoke_backend.py \
		--endpoint http://127.0.0.1:$(DOCKER_SMOKE_PORT) --origin "$(PAIZIQ_CORS_ORIGINS)"; \
	STATUS=$$?; docker rm -f paiziq-ingest-smoke >/dev/null 2>&1 || true; exit $$STATUS

smoke: ## Smoke-test a running backend (PAIZIQ_ENDPOINT, PAIZIQ_API_KEY, optional PAIZIQ_DASHBOARD_URL)
	@PAIZIQ_API_KEY="$(PAIZIQ_API_KEY)" python3 $(INGEST_DIR)/scripts/smoke_backend.py \
		--endpoint "$(PAIZIQ_ENDPOINT)" $(if $(PAIZIQ_DASHBOARD_URL),--origin "$(PAIZIQ_DASHBOARD_URL)",)

northstar-demo: ## Run the Northstar payment-agent demo with the real SDK against PAIZIQ_ENDPOINT
	@cd $(INGEST_DIR) && PAIZIQ_API_KEY="$(PAIZIQ_API_KEY)" ../../$(VENV)/bin/python scripts/northstar_demo.py \
		--endpoint "$(PAIZIQ_ENDPOINT)" $(if $(PAIZIQ_DASHBOARD_URL),--dashboard-url "$(PAIZIQ_DASHBOARD_URL)",)

deploy-azure: ## Deploy the backend to Azure Container Apps (see deploy/azure/backend.env.example)
	./deploy/azure/deploy_backend.sh

check: lint test ingest-test examples ## Full quality gate (lint + all tests + examples)
	@echo "All checks passed."

clean: ## Remove build artifacts and caches
	rm -rf $(SDK_DIR)/dist $(SDK_DIR)/build $(SDK_DIR)/src/*.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} \;
