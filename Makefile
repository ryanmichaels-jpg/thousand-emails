.PHONY: seed up down load schema test demo lint
DB ?= postgresql://thousand:thousand@localhost:5432/thousand

seed:            ## regenerate seed/fixtures from seed/seed.yaml
	python seed/generate.py
up:              ## start postgres + api
	docker compose up -d --build
down:
	docker compose down
load:            ## load fixtures into raw_* schemas
	DATABASE_URL=$(DB) python seed/load.py
schema:          ## apply app schema
	psql $(DB) -v ON_ERROR_STOP=1 -f db/schema.sql
test:
	pytest -q
lint:
	ruff check api tests seed
demo: seed up load schema  ## full local demo (stages add: score, patch, draft, web)
	@echo "next: python -m api.scoring.run && python -m api.patch.cut && python -m api.drafts.run --rep sdr_1 --limit 50"
