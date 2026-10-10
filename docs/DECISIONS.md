# Decisions (ADR style: context, decision, consequences)

## ADR-001: Build in place under `flowjob/`
- Context: The prompt pack setup says `mkdir reelforge`, but the working repo is `flowjob/`.
- Decision: Build in place. Package layout is `app/...` either way, so the folder name is cosmetic.
- Consequences: All paths in docs refer to the repo root as-is.

## ADR-002: uv-managed Python 3.12
- Context: Only Python 3.14 exists on the host; the spec pins Python 3.12 and ML wheels (torch, faster-whisper) lag on new interpreters.
- Decision: `uv python install 3.12` + `.python-version` = 3.12; `requires-python ==3.12.*`.
- Consequences: Reproducible interpreter in CI and Docker (`python:3.12-slim`).

## ADR-003: pgvector image `pgvector/pgvector:pg16`
- Context: Need Postgres + pgvector for asset embeddings (Phase 7 user-library search is removed per locked decision 3, but embeddings are still needed for CLIP ranking caches and future use).
- Decision: `pgvector/pgvector:pg16` for the `db` service.
- Consequences: `pgvector` extension available via migration (`CREATE EXTENSION IF NOT EXISTS vector`).

## ADR-005: Garage instead of MinIO for local S3-compatible storage
- Context: Blueprint says MinIO local. MinIO community edition is archived/EOL (Feb 2026); `minio/minio` is gone from Docker Hub and anonymous Quay pulls now return 401 (verified 2026-10-09; see https://www.stablebuild.com/blog/minio-pulls-from-quay-now-return-unauthorized and https://github.com/minio/minio).
- Decision: Single-node Garage v2.4.1 (`dxflrs/garage:v2.4.1`) for local dev only; docs: https://garagehq.deuxfleurs.fr/documentation/quick-start/ (retrieved 2026-10-09). One-shot `garage-init` container assigns layout, creates bucket `reelforge`, imports static dev creds (minioadmin/minioadmin). Prod still R2/S3 via the same `Storage` abstraction — no app code changes.
- Consequences: `S3_ENDPOINT` default unchanged (`http://localhost:9000` host-mapped to Garage's `:3900`); Garage is AGPLv3 — acceptable for local dev, never shipped to prod.

## ADR-004: mypy strict globally in Phase 0
- Context: Spec requires strict only on `app/timeline` and `app/tools`.
- Decision: `strict = true` for all of `app/` from the start; relax per-module only with a recorded reason.
- Consequences: More annotation work up front, fewer type surprises later.
