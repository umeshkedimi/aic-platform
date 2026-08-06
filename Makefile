.DEFAULT_GOAL := help

COMPOSE := docker compose --env-file .env -f docker/docker-compose.yml

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'

.PHONY: up
up: ## Start the full stack (postgres, redis, qdrant, minio, api)
	$(COMPOSE) up --build -d
	@echo "waiting for api readiness..."
	@for i in $$(seq 1 30); do \
		curl -sf http://localhost:8000/v1/health/ready >/dev/null && echo "ready" && exit 0; \
		sleep 2; \
	done; \
	echo "api did not become ready in time — run 'make logs'" && exit 1

.PHONY: down
down: ## Stop the stack and remove containers (volumes persist)
	$(COMPOSE) down

.PHONY: logs
logs: ## Tail logs from all services
	$(COMPOSE) logs -f

.PHONY: lint
lint: ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

.PHONY: typecheck
typecheck: ## Mypy --strict
	uv run mypy app

.PHONY: imports
imports: ## Verify the layer/import contracts (ADR-0001, ADR-0002)
	uv run lint-imports

.PHONY: test
test: ## Run the test suite (unit + integration; integration needs Docker)
	uv run pytest

.PHONY: check
check: lint typecheck imports test ## Everything CI runs: lint + typecheck + imports + test

.PHONY: migrate
migrate: ## Apply database migrations
	uv run alembic upgrade head

.PHONY: fmt
fmt: ## Auto-fix lint + format issues
	uv run ruff check --fix .
	uv run ruff format .
