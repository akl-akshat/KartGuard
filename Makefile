# ReturnGuard developer convenience targets.
.DEFAULT_GOAL := help
.PHONY: help install up down logs schema seed embed test test-unit test-safety eval smoke api worker lint fmt

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install the package + dev extras into the active environment
	pip install -e ".[dev]"

up: ## Start Postgres(+pgvector) and Kafka
	docker compose up -d

down: ## Stop and remove backing services
	docker compose down

logs: ## Tail backing-service logs
	docker compose logs -f

schema: ## Apply the database schema
	psql "$$DATABASE_URL" -f db/schema.sql

seed: ## Load synthetic data
	python -m db.seed

embed: ## Chunk + embed the policy corpus into pgvector
	python -m policies.embed

test: ## Run the full test suite
	pytest

test-unit: ## Run pure-logic unit tests (no infra)
	pytest -m unit

test-safety: ## Run safety/guardrail/idempotency tests
	pytest -m safety

eval: ## Run the evaluation harness with hard gates
	python -m eval.runner

smoke: ## Run the cumulative smoke test
	bash scripts/smoke.sh

api: ## Run the FastAPI service
	uvicorn service.app:app --host $${API_HOST:-0.0.0.0} --port $${API_PORT:-8000} --reload

worker: ## Run the Kafka event worker
	python -m events.consumer

lint: ## Lint with ruff
	ruff check .

fmt: ## Auto-format with ruff
	ruff format . && ruff check --fix .
