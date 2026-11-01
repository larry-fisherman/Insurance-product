UV ?= $(CURDIR)/.tools/uv/bin/uv
export UV_CACHE_DIR := $(CURDIR)/.tools/cache/uv
export UV_PYTHON_INSTALL_DIR := $(CURDIR)/.tools/python
export npm_config_cache := $(CURDIR)/.tools/cache/npm
BACKEND := services/backend
FULL_COMPOSE := docker compose --env-file $(BACKEND)/.env.app -f compose.full.yaml
DB_COMPOSE := docker compose --env-file $(BACKEND)/.env.database -f compose.database.yaml

.PHONY: bootstrap bootstrap-api install dev-api dev-web check check-api check-web contracts smoke up down db-config db-up db-down db-migrate db-migrate-native check-db db-check

bootstrap:
	python3 -m venv .tools/uv
	.tools/uv/bin/python -m pip install --no-cache-dir uv==0.13.6
	$(MAKE) install

bootstrap-api:
	python3 -m venv .tools/uv
	.tools/uv/bin/python -m pip install --no-cache-dir uv==0.13.6
	$(UV) sync --project $(BACKEND) --python 3.13 --frozen

install:
	npm ci
	$(UV) sync --project $(BACKEND) --python 3.13 --frozen

dev-api:
	$(UV) run --project $(BACKEND) --frozen uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000

dev-web:
	npm run dev

check: check-api check-web

check-api:
	$(UV) run --project $(BACKEND) --frozen ruff check $(BACKEND)
	$(UV) run --project $(BACKEND) --frozen ruff format --check $(BACKEND)
	cd $(BACKEND) && $(UV) run --frozen mypy
	$(UV) run --project $(BACKEND) --frozen pytest -m "not database" $(BACKEND)/tests

check-web:
	npm run format:check
	npm run lint
	npm run typecheck
	npm test

contracts:
	$(UV) run --project $(BACKEND) --frozen python -m app.export_openapi packages/contracts/openapi.json
	npm run contracts:generate

smoke:
	npm run smoke

up:
	docker compose up --build --wait

down:
	docker compose down

db-config:
	python3 $(BACKEND)/app/database/local_setup.py

db-up:
	$(DB_COMPOSE) up -d --wait postgres

db-down:
	$(DB_COMPOSE) down

db-migrate:
	$(DB_COMPOSE) run --build --rm migrate

db-migrate-native:
	$(UV) run --project $(BACKEND) --frozen alembic -c $(BACKEND)/alembic.ini upgrade head

db-check:
	$(UV) run --project $(BACKEND) --frozen alembic -c $(BACKEND)/alembic.ini check

check-db:
	$(UV) run --project $(BACKEND) --frozen pytest -m database $(BACKEND)/tests

app-config:
	$(UV) run --project $(BACKEND) --frozen python -m app.bootstrap configure

app-roles:
	$(UV) run --project $(BACKEND) --frozen python -m app.bootstrap provision

full-up: db-config db-up app-config app-roles db-migrate-native
	$(FULL_COMPOSE) up -d --build redis parser api worker scheduler web inquiries marketing
	$(FULL_COMPOSE) up -d --wait --wait-timeout 120 redis parser api web inquiries marketing

full-down:
	$(FULL_COMPOSE) down

scan-up:
	$(FULL_COMPOSE) --profile scanning up -d clamav

full-logs:
	$(FULL_COMPOSE) logs --tail=100 api worker scheduler

evaluate:
	$(UV) run --project $(BACKEND) --frozen python -m app.evaluate

backup-local:
	$(UV) run --project $(BACKEND) --frozen python -m app.operations backup .artifacts/backup-local

restore-drill:
	$(UV) run --project $(BACKEND) --frozen python -m app.operations drill .artifacts/backup-local

browser-install:
	PLAYWRIGHT_BROWSERS_PATH="$(CURDIR)/.tools/browsers" npm exec playwright -- install chromium

check-e2e:
	PLAYWRIGHT_BROWSERS_PATH="$(CURDIR)/.tools/browsers" npm run test:e2e

verify-ocr:
	$(FULL_COMPOSE) exec -T worker python - < scripts/verify-ocr.py

.PHONY: full-up full-down scan-up full-logs app-config app-roles evaluate backup-local restore-drill browser-install check-e2e verify-ocr

verify-platform:
	python3 scripts/verify-platform-boundaries.py

.PHONY: verify-platform
