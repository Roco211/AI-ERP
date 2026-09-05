UV ?= uv
PNPM ?= pnpm
.PHONY: env install infra migrate seed seed-sales api web worker beat lint test contract check down
env:
	python3 scripts/dev_env.py
install:
	$(UV) sync --project apps/api --locked
	$(PNPM) install --frozen-lockfile
infra:
	docker compose up -d --wait
migrate:
	$(UV) run --project apps/api alembic -c apps/api/alembic.ini upgrade head
seed:
	$(UV) run --project apps/api python apps/api/scripts/seed.py
seed-catalog:
	$(UV) run --project apps/api python apps/api/scripts/seed_catalog.py
api:
	$(UV) run --project apps/api uvicorn forge_erp.main:app --host 127.0.0.1 --port 8100 --no-access-log
web:
	$(PNPM) dev
worker:
	$(UV) run --project apps/api celery -A forge_erp.workers.celery_app worker --loglevel=INFO
beat:
	$(UV) run --project apps/api celery -A forge_erp.workers.celery_app beat --loglevel=INFO
lint:
	$(UV) run --project apps/api ruff check apps/api
	$(UV) run --project apps/api ruff format --check apps/api
	$(UV) run --project apps/api pyright --project apps/api
	$(PNPM) lint
	$(PNPM) typecheck
test:
	$(UV) run --project apps/api pytest apps/api/tests -q
	$(PNPM) test
contract:
	$(UV) run --project apps/api python apps/api/scripts/export_openapi.py
	$(PNPM) api:generate
check: lint test
	$(PNPM) build
	$(PNPM) test:e2e
down:
	docker compose down

semantic-infra:
	docker compose --profile semantic up -d --wait ollama
semantic-pull:
	docker compose exec -T ollama ollama pull bge-m3:567m
semantic-rebuild:
	$(UV) run --project apps/api python apps/api/scripts/rebuild_embeddings.py --organization DEMO
semantic-eval:
	$(UV) run --project apps/api python apps/api/scripts/eval_embeddings.py
semantic-enable:
	$(UV) run --project apps/api python apps/api/scripts/configure_embeddings.py

seed-inventory:
	$(UV) run --project apps/api python apps/api/scripts/seed_inventory.py

seed-purchasing:
	$(UV) run --project apps/api python apps/api/scripts/seed_purchasing.py

seed-sales:
	$(UV) run --project apps/api python apps/api/scripts/seed_sales.py
