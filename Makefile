.PHONY: dev check worker golden eval-live assets down logs

dev: ## docker compose up (mongo, redis) + API with reload
	docker compose up --build -d
	docker compose ps

check: ## ruff + mypy + pytest -m "not integration" (MUST pass before any phase is "done")
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy app
	uv run pytest -m "not integration" -q

worker: ## arq worker(s)
	docker compose up --build worker-cpu

golden: ## render golden tests, compare ffprobe facts + SSIM on sampled frames
	uv run pytest tests/golden -q

eval-live: ## live-model evals (opt-in, needs provider keys)
	uv run pytest tests/evals -m integration -q -s

assets: ## one-time download of the local music/image/gif/meme libraries (licensed, see ADR-014)
	uv run python scripts/fetch_assets.py

down:
	docker compose down

logs:
	docker compose logs -f --tail=200
