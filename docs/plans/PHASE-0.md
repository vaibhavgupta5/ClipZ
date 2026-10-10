# PHASE-0 Implementation Plan: Foundation and Scaffolding

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A running, tested skeleton with local infra (Postgres+pgvector, Redis, MinIO) and CI.

**Architecture:** FastAPI API + Arq CPU worker, both in Docker; SQLAlchemy 2 + Alembic for the 8-table schema; S3-compatible storage abstraction; provider interfaces as Protocols with fakes. Python 3.12 via uv.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 + Alembic + pgvector, Arq + Redis, boto3 (S3/MinIO), pydantic-settings, structlog, OpenTelemetry SDK (no-op default), pytest + pytest-asyncio, ruff, mypy strict, pre-commit.

**Spec:** `docs/BLUEPRINT.md` (product blueprint v2); phase tasks from `prompt.md` §6 Phase 0.

## Global Constraints

- Python 3.12. No network in unit tests (fakes/fixtures only); integration tests marked `integration` and opt-in.
- Max video duration 90 s enforced at upload + Timeline validation (Phase 2/1 respectively — schema carries the constant now).
- Languages: en, hi. Every asset carries license metadata (schema now, enforcement Phase 1+).
- `make check` = ruff + mypy + `pytest -m "not integration"`; MUST pass before phase is done.
- Conventional commits, one logical change per commit.

## Review Focus

- `/readyz` reporting ready while a dependency is down (must reflect DB/Redis/storage truthfully) → test in Task 4.
- Alembic downgrade leaving the DB unusable for re-upgrade → test upgrade→downgrade→upgrade cycle in Task 5.
- Presigned URL expiry/security (URL must actually work against MinIO, short TTL) → integration test in Task 6.
- `.env.example` drifting from `Settings` fields (undocumented var = broken deploy) → test in Task 7 that every Settings field is documented.
- Fixture durations wrong (89 s / 91 s boundary is load-bearing for Phase 2) → assert with ffprobe in Task 8.

---

### Task 1: Repo scaffold + tooling

**Files:**
- Create: `pyproject.toml`, `.python-version`, `Makefile`, `.pre-commit-config.yaml`, `.gitignore`, `.dockerignore`, `CLAUDE.md`, `docs/BLUEPRINT.md` (copy of product.md), `docs/DECISIONS.md`, `docs/PROGRESS.md`
- Create package stubs: `app/__init__.py`, `app/api/__init__.py`, `app/agent/__init__.py`, `app/tools/__init__.py`, `app/timeline/__init__.py`, `app/render/__init__.py`, `app/analysis/__init__.py`, `app/inserts/__init__.py`, `app/style/__init__.py`, `app/trends/__init__.py`, `app/workers/__init__.py`, `app/mcp_server/__init__.py`, `app/providers/__init__.py`
- Create: `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`, `tests/golden/__init__.py`

**Interfaces:**
- Produces: `make check` (ruff + mypy + pytest unit), `make dev`, `make worker`, `make golden` (stub passing) targets; `uv` project named `reelforge` requiring Python 3.12.

- [ ] **Step 1:** Write `pyproject.toml` (uv project, deps: fastapi, uvicorn, sqlalchemy[asyncio]+asyncpg, alembic, arq, redis, boto3, pydantic v2, pydantic-settings, structlog, opentelemetry SDK pkgs, pytest/pytest-asyncio/httpx/anyio in dev; ruff + mypy config, pytest `integration` marker).
- [ ] **Step 2:** Write `Makefile`, `.python-version` (`3.12`), `.gitignore`, `.dockerignore`, `.pre-commit-config.yaml`, `CLAUDE.md` (verbatim from prompt.md §3), `docs/DECISIONS.md` (ADR-001..003: in-place build, uv-managed 3.12, pgvector pg16 image), `docs/PROGRESS.md` (Phase 0 in progress).
- [ ] **Step 3:** Run `uv venv --python 3.12 && uv sync` then `make check`. Expected: PASS (no tests yet → pytest exit 5? configure `addopts` so empty suite passes or add a placeholder test; choose placeholder `tests/unit/test_sanity.py::test_import_app`).
- [ ] **Step 4:** Commit: `git add -A && git commit -m "chore: scaffold reelforge repo with uv, ruff, mypy, pytest"`.

### Task 2: docker-compose + Dockerfiles

**Files:**
- Create: `docker-compose.yml`, `Dockerfile`, `.env.example` (draft; finalized Task 7)

**Interfaces:**
- Consumes: service names `db` (pgvector/pgvector:pg16), `redis` (redis:7-alpine), `minio` (minio/minio), `api`, `worker-cpu`.
- Produces: `make dev` → all healthy; `make worker` → arq worker running.

- [ ] **Step 1:** Write `Dockerfile` (python:3.12-slim, ffmpeg installed via apt for worker parity, `uv sync --frozen` layer caching) and `docker-compose.yml` (healthchecks on db/redis/minio; api depends on healthy deps; worker-cpu runs `arq app.workers.main.WorkerSettings`).
- [ ] **Step 2:** Run `make dev` (build + up) and `curl localhost:8000/healthz`. Expected: `{"status":"ok"}` (works only after Task 4; at this task's end, containers build and db/redis/minio are healthy — record evidence in gate doc).
- [ ] **Step 3:** Commit: `git commit -m "chore: add docker compose stack and Dockerfiles"`.

### Task 3: Settings, logging, OTEL bootstrap

**Files:**
- Create: `app/settings.py`, `app/logging_setup.py`, `app/otel.py`
- Test: `tests/unit/test_settings.py`

**Interfaces:**
- Produces: `get_settings() -> Settings` (cached; every field has `Field(description=...)` and matching `.env.example` entry); `setup_logging()` (structlog JSON, request-id bound); `setup_tracing()` (no-op exporter unless `OTEL_EXPORTER_OTLP_ENDPOINT` set).

- [ ] **Step 1: Write the failing test** `test_every_setting_documented_in_env_example` — parses `.env.example` keys, asserts set equality with `Settings.model_fields`.
- [ ] **Step 2:** Run it. Expected: FAIL (no Settings yet).
- [ ] **Step 3:** Implement `app/settings.py` (DATABASE_URL, REDIS_URL, S3_*, ANTHROPIC_API_KEY/GEMINI_API_KEY optional, MUSIC/GIF/STOCK/MEME/IMAGEGEN provider keys optional, MAX_VIDEO_SECONDS=90, DEFAULT_CAPTION_SCRIPT, GPU_WORKER_URL, SENTRY_DSN, OTEL_*), `app/logging_setup.py`, `app/otel.py`; write `.env.example`.
- [ ] **Step 4:** Run `pytest tests/unit/test_settings.py`. Expected: PASS.
- [ ] **Step 5:** Commit: `git commit -m "feat: add settings, structured logging, otel bootstrap"`.

### Task 4: FastAPI app + /healthz + /readyz

**Files:**
- Create: `app/main.py`, `app/api/routes.py`, `app/db.py` (engine/session helpers)
- Test: `tests/unit/test_health.py`

**Interfaces:**
- Consumes: `get_settings()`, storage client factory (Task 6 — use a Protocol + fake here; readyz checks real clients via dependency injection overridable in tests).
- Produces: `GET /healthz → {"status":"ok"}` (no deps); `GET /readyz → {"status":"ready","checks":{db,redis,storage}}` with 503 + per-check detail when any dependency fails.

- [ ] **Step 1: Write the failing tests** — healthz 200; readyz 200 when deps ok (fake clients); readyz 503 with `{"checks":{"db":"down"...}}` when DB check raises (Review Focus #1).
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `app/main.py` (request-ID middleware binding structlog context), `app/api/routes.py`, `app/db.py`.
- [ ] **Step 4:** Run `pytest tests/unit/test_health.py` + `ruff` + `mypy`. Expected: PASS.
- [ ] **Step 5:** Commit: `git commit -m "feat: add FastAPI app with healthz and readyz"`.

### Task 5: SQLAlchemy models + Alembic

**Files:**
- Create: `app/models.py`, `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/0001_initial.py`
- Test: `tests/unit/test_models.py` (SQLite in-memory? No — pgvector/UUID/DATERANGE need Postgres. Use testcontainers? No network-free rule applies to unit tests, but testcontainers needs docker socket. Decision: model tests run against a transactional Postgres via `TEST_DATABASE_URL`, marked `integration`. Pure-schema assertions (table names, columns, license fields present) run network-free by inspecting `Base.metadata` → unit test.)

**Interfaces:**
- Produces: tables `users, projects, assets (source, license, attribution_required, attribution_text, embedding vector(512) nullable), timelines, timeline_versions (number, edl JSONB, parent_version), messages, jobs (type, status, progress, idempotency_key unique), renders (timeline_hash, profile, status, output_url)`; `alembic upgrade head` + `downgrade -1` round-trip (Review Focus #2).

- [ ] **Step 1: Write the failing unit test** — asserts all 8 tables exist in metadata with required columns (license fields on assets, unique idempotency_key on jobs).
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `app/models.py` (SQLAlchemy 2 typed mappings, pgvector `Vector(512)`), Alembic env + `0001_initial.py` (upgrade AND downgrade).
- [ ] **Step 4:** Run unit test; then against compose db: `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` (integration, manual evidence for gate doc).
- [ ] **Step 5:** Commit: `git commit -m "feat: add core data model and initial migration"`.

### Task 6: Storage abstraction + presigned URLs

**Files:**
- Create: `app/storage.py`
- Test: `tests/unit/test_storage.py` (fake in-memory backend), `tests/integration/test_storage_minio.py` (real MinIO: presigned PUT → PUT bytes → GET round-trip, Review Focus #3)

**Interfaces:**
- Produces: `Storage` Protocol (`presigned_upload_url(key, content_type, expires_s=3600)`, `presigned_download_url(key)`, `put_bytes`, `get_bytes`, `delete`); `S3Storage(dsn...)` impl (boto3, `create_bucket_if_not_exists`); `MemoryStorage` fake for unit tests.

- [ ] **Step 1: Write failing unit tests** against `MemoryStorage` + presigned-URL shape assertions for `S3Storage` with stubbed client (botocore Stubber — no network).
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `app/storage.py` (argument-list-free boto3 calls, path-safe key validation rejecting `..`, max key length).
- [ ] **Step 4:** `pytest -m "not integration"` PASS; then `pytest -m integration` against `make dev` MinIO. Expected: PASS.
- [ ] **Step 5:** Commit: `git commit -m "feat: add S3-compatible storage abstraction"`.

### Task 7: Provider interfaces + fakes

**Files:**
- Create: `app/providers/base.py`, `app/providers/fakes.py`
- Test: `tests/unit/test_providers.py`

**Interfaces:**
- Produces: `LLMProvider, MusicProvider, ImageProvider, GifProvider, MemeTemplateProvider, SfxProvider` Protocols (async methods, pydantic in/out, `provider_name`); `Fake*` impls returning deterministic fixtures; `provider_registry()` mapping names → impls (env-selected, default fake).

- [ ] **Step 1: Write failing tests** — each fake returns schema-valid objects; registry resolves `PROVIDER=FakeX` default.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement `base.py` + `fakes.py` (business logic must never import vendor SDKs — add a grep assertion in CI? add `tests/unit/test_no_vendor_imports.py` scanning `app/` for `anthropic|google.generativeai|openai` imports outside `app/providers/`).
- [ ] **Step 4:** Run full `make check`. Expected: PASS.
- [ ] **Step 5:** Commit: `git commit -m "feat: add provider interfaces and fakes"`.

### Task 8: Fixture generator + CI

**Files:**
- Create: `scripts/make_fixtures.sh`, `.github/workflows/ci.yml`
- Test: manual ffprobe assertions recorded in gate doc (Review Focus #5: 89 s vs 91 s boundary precision).

**Interfaces:**
- Produces: `fixtures/synth/{bars_5s,bars_30s,bars_89s,bars_91s,vfr,rotated}.mp4` (lavfi smpte/ mandelbrot + sine + burned frame counter); CI runs `make check` on push/PR.

- [ ] **Step 1:** Write script (testsrc2 + sine, `-r 30` CFR except VFR variant, rotate metadata variant, exact durations).
- [ ] **Step 2:** Run script; ffprobe each: duration within ±40 ms of target, fps constant (except VFR), rotation tag present. Expected: PASS.
- [ ] **Step 3:** Write CI workflow; validate YAML (`python -c yaml.safe_load`).
- [ ] **Step 4:** Commit: `git commit -m "chore: add lavfi fixture generator and CI workflow"`.

### Task 9: Gate

- [ ] Run `make check` green; `make golden` stub passes; Alembic round-trip on clean DB; MinIO presigned round-trip.
- [ ] Write `docs/gates/PHASE-0.md` (commands run, results, gaps) + update `docs/PROGRESS.md`.
- [ ] Commit: `git commit -m "docs: phase 0 gate report"`.
