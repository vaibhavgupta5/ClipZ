"""Health endpoint tests: healthz is dependency-free; readyz reflects dependency truth."""

import pytest
from fastapi.testclient import TestClient

from app.api import health as health_module
from app.main import create_app


def _patched_client(
    monkeypatch: pytest.MonkeyPatch,
    db_ok: bool = True,
    redis_ok: bool = True,
    storage_ok: bool = True,
) -> TestClient:
    async def maybe(name: str, ok: bool) -> str:
        if not ok:
            raise ConnectionError(f"{name} unreachable")
        return "ok"

    async def db() -> str:
        return await maybe("db", db_ok)

    async def redis() -> str:
        return await maybe("redis", redis_ok)

    async def storage() -> str:
        return await maybe("storage", storage_ok)

    monkeypatch.setattr(health_module, "check_db", db)
    monkeypatch.setattr(health_module, "check_redis", redis)
    monkeypatch.setattr(health_module, "check_storage", storage)
    return TestClient(create_app(), raise_server_exceptions=False)


def test_healthz_needs_no_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _patched_client(monkeypatch, db_ok=False, redis_ok=False, storage_ok=False).get(
        "/healthz"
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_ready_when_all_deps_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _patched_client(monkeypatch).get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ready",
        "checks": {"db": "ok", "redis": "ok", "storage": "ok"},
    }


def test_readyz_503_names_failing_dep(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _patched_client(monkeypatch, db_ok=False).get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["db"] != "ok"
    assert body["checks"]["redis"] == "ok"


def test_request_id_header_present(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _patched_client(monkeypatch).get("/healthz")
    assert resp.headers.get("X-Request-ID")


async def test_readyz_uses_real_checkers_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # Real checkers against dead ports must report down, not crash (no ambient infra assumed).
    from httpx import ASGITransport, AsyncClient

    from app.settings import Settings

    dead = Settings(
        TIGER_DATABASE_URL="postgresql://x:y@127.0.0.1:1/none",
        REDIS_URL="redis://127.0.0.1:1/0",
        UPLOAD_DIR="/proc/clipz-not-writable",
    )
    monkeypatch.setattr(health_module, "get_settings", lambda: dead)
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"
