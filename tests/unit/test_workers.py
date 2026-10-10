"""Worker tests: arq settings valid, health_ping task works."""

from app.workers.main import WorkerSettings, health_ping


def test_worker_has_at_least_one_function() -> None:
    assert len(WorkerSettings.functions) >= 1


async def test_health_ping() -> None:
    assert await health_ping({}) == {"status": "ok"}  # type: ignore[typeddict-item]
