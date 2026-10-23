# TruthGuard — common tasks. `make help` lists them.
SHELL := /bin/bash
PY := backend/.venv/bin/python
API_PORT ?= 8000
WEB_PORT ?= 3000

.PHONY: help install up down logs dev api web test test-pg lint fmt typecheck e2e eval eval-dev generate seed demo preflight reset migrate clean

help: ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## install backend (uv venv) and frontend (npm) dependencies
	cd backend && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
	cd frontend && npm ci

up: ## docker compose: Postgres+pgvector, API on :8000, UI on :3000 (seeds on first start)
	docker compose up -d --build
	@echo "UI http://localhost:3000  API http://localhost:8000/docs"

down: ## stop the compose stack
	docker compose down

logs: ## follow compose logs
	docker compose logs -f

api: ## run the API locally (SQLite, auto-seeds the sample corpus)
	cd backend && .venv/bin/uvicorn truthguard.api.app:app_factory --factory --reload --port $(API_PORT)

web: ## run the UI locally against the local API
	cd frontend && NEXT_PUBLIC_API_URL=http://localhost:$(API_PORT) npm run dev

dev: ## API + UI locally, no Docker
	$(MAKE) -j2 api web

test: ## backend unit + integration tests with coverage
	cd backend && .venv/bin/pytest --cov --cov-report=term-missing:skip-covered

test-pg: ## the PostgreSQL + pgvector integration test (needs a database with the vector extension)
	cd backend && TEST_POSTGRES_URL=$${TEST_POSTGRES_URL:-postgresql+asyncpg://postgres:postgres@localhost:5432/truthguard_test} .venv/bin/pytest tests/integration/test_postgres.py

lint: ## ruff (backend, eval, scripts) + eslint (frontend)
	cd backend && .venv/bin/ruff check src tests ../eval ../scripts && .venv/bin/ruff format --check src tests ../eval ../scripts
	cd frontend && npx next lint

fmt: ## auto-format
	cd backend && .venv/bin/ruff format src tests ../eval ../scripts && .venv/bin/ruff check --fix src tests ../eval ../scripts

typecheck: ## mypy --strict + tsc
	cd backend && .venv/bin/mypy
	cd frontend && npx tsc --noEmit

e2e: ## Playwright: the 5-minute demo script end to end (builds the UI first)
	cd frontend && npm run build && CI=1 npx playwright test

generate: ## regenerate the synthetic corpus, manifest and gold questions (deterministic)
	$(PY) eval/generator/generate.py

eval: ## FINAL numbers on the frozen test split -> eval/reports/results.md
	$(PY) -m eval.runners.run_eval --split test --out eval/reports

eval-dev: ## tuning numbers on the dev split -> eval/reports/dev/
	$(PY) -m eval.runners.run_eval --split dev --out eval/reports/dev

seed: ## ingest the sample corpus into DATABASE_URL (idempotent)
	cd backend && .venv/bin/truthguard seed

migrate: ## alembic upgrade head on DATABASE_URL
	cd backend && .venv/bin/alembic upgrade head

demo: ## fresh-clone demo: compose stack in DEMO_MODE with "today" pinned, then preflight
	DEMO_MODE=true TODAY_OVERRIDE=$${TODAY_OVERRIDE:-2026-10-23} docker compose up -d --build
	@for i in $$(seq 1 90); do curl -sf localhost:$(API_PORT)/readyz >/dev/null && break; sleep 2; done
	$(MAKE) preflight
	@echo "Open http://localhost:$(WEB_PORT)"

preflight: ## green/red checks + 3 canary queries; run 10 minutes before presenting
	$(PY) scripts/preflight.py

reset: ## remove judge-added documents and restore the default precedence policy
	curl -s -X POST localhost:$(API_PORT)/v1/admin/reset && echo

clean: ## remove local databases, caches and build output
	rm -rf backend/*.db backend/.cache eval/.llm_cache frontend/.next frontend/test-results frontend/playwright-report
