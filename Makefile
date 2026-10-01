# Easy Chain developer commands. Needs: uv (Python), pnpm (Node 20+).
.PHONY: help install dev server worker web build test test-python test-web test-client e2e lint format schema golden docker clean

help:
	@echo "make install   install Python and web dependencies"
	@echo "make dev       run the API (port 8000) and the web dev server (port 5173)"
	@echo "make build     build the web app into the Python package (one-process app)"
	@echo "make server    run the built app on http://127.0.0.1:8000"
	@echo "make worker    run a separate worker (start the server with EASYCHAIN_WORKER=off)"
	@echo "make test      Python tests + web and client unit tests"
	@echo "make e2e       Playwright end-to-end tests (builds the web app first)"
	@echo "make lint      ruff + TypeScript checks"
	@echo "make schema    regenerate spec/flow.schema.json"
	@echo "make golden    regenerate compiler golden files (review the diff!)"
	@echo "make docker    docker compose up --build (Postgres + API + worker)"

install:
	cd python && uv sync
	pnpm install

dev:
	@echo "Easy Chain: open http://localhost:5173"
	@trap 'kill 0' INT TERM EXIT; \
	(cd python && uv run easychain dev --port 8000 --reload) & \
	pnpm --filter @easychain/web dev & \
	wait

server: build
	cd python && uv run easychain dev --port 8000

worker:
	cd python && uv run easychain worker --verbose

web:
	pnpm --filter @easychain/web build

build: web
	rm -rf python/src/easychain/server/static/assets python/src/easychain/server/static/index.html
	cp -r apps/web/dist/. python/src/easychain/server/static/

test: test-python test-web test-client

test-python:
	cd python && uv run pytest --cov=easychain --cov-report=term-missing:skip-covered

test-web:
	pnpm --filter @easychain/web test

test-client:
	pnpm --filter @easychain/client test

e2e: web
	pnpm --filter @easychain/web e2e

lint:
	cd python && uv run ruff check src tests && uv run ruff format --check src tests
	pnpm --filter @easychain/web typecheck
	pnpm --filter @easychain/client typecheck

format:
	cd python && uv run ruff format src tests && uv run ruff check --fix src tests

schema:
	cd python && uv run easychain schema -o ../spec/flow.schema.json

golden:
	cd python && UPDATE_GOLDEN=1 uv run pytest tests/test_compiler_golden.py

docker:
	docker compose up --build

clean:
	rm -rf apps/web/dist apps/web/test-results apps/web/playwright-report python/.pytest_cache python/.coverage
	find python/src/easychain/server/static -mindepth 1 ! -name .gitkeep -delete
