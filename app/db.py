"""Tiger Data (PostgreSQL + TimescaleDB) access via asyncpg (ADR-016).

Typed helpers map tables to the pydantic models in app/models.py, so routes and
workers never write SQL by hand. Filters are equality only, and every column name
is checked against the model before it reaches SQL; values are always bound
parameters. Schema lives in app/sql/*.sql, applied in order by ``migrate``.
"""

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import asyncpg

from app.models import Doc
from app.settings import Settings, get_settings

SQL_DIR = Path(__file__).parent / "sql"


class DuplicateKeyError(Exception):
    """A unique constraint rejected the write (idempotency keys, version numbers...)."""


async def _init_conn(conn: asyncpg.Connection) -> None:
    for typ in ("jsonb", "json"):
        await conn.set_type_codec(typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


class Database:
    """Lazy asyncpg pool bound to one event loop (pools are loop-affine)."""

    def __init__(self, dsn: str, schema: str) -> None:
        self.dsn = dsn
        self.schema = schema
        self._pool: asyncpg.Pool | None = None

    async def pool(self) -> asyncpg.Pool:
        """Create the pool on first use."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self.dsn,
                min_size=1,
                max_size=10,
                timeout=5,
                command_timeout=30,
                init=_init_conn,
                server_settings={"search_path": f"{self.schema},public"},
            )
        return self._pool

    async def execute(self, sql: str, *args: Any) -> str:
        """Run one statement."""
        pool = await self.pool()
        try:
            return str(await pool.execute(sql, *args))
        except asyncpg.UniqueViolationError as exc:
            raise DuplicateKeyError(str(exc)) from None

    async def fetch(self, sql: str, *args: Any) -> list[asyncpg.Record]:
        """Rows for a query."""
        return list(await (await self.pool()).fetch(sql, *args))

    async def close(self) -> None:
        """Close the pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


_dbs: dict[tuple[int, str, str], Database] = {}


def get_db(settings: Settings | None = None) -> Database:
    """Database handle for the running event loop."""
    settings = settings or get_settings()
    key = (id(asyncio.get_running_loop()), settings.TIGER_DATABASE_URL, settings.TIGER_SCHEMA)
    db = _dbs.get(key)
    if db is None:
        db = Database(settings.TIGER_DATABASE_URL, settings.TIGER_SCHEMA)
        _dbs[key] = db
    return db


# --- typed document helpers ---------------------------------------------------------
def _col(model: type[Doc], field: str) -> str:
    name = "id" if field == "_id" else field
    if name not in model.model_fields:
        raise ValueError(f"{model.__name__} has no column {field!r}")
    return f'"{name}"'


def _where(model: type[Doc], query: Mapping[str, Any]) -> tuple[str, list[Any]]:
    parts: list[str] = []
    args: list[Any] = []
    for field, value in query.items():
        if value is None:
            parts.append(f"{_col(model, field)} IS NULL")
        else:
            args.append(value)
            parts.append(f"{_col(model, field)} = ${len(args)}")
    return (" WHERE " + " AND ".join(parts)) if parts else "", args


def _order(model: type[Doc], sort: list[tuple[str, int]] | None) -> str:
    if not sort:
        return ""
    return " ORDER BY " + ", ".join(
        f"{_col(model, f)} {'DESC' if d < 0 else 'ASC'}" for f, d in sort
    )


async def insert[D: Doc](db: Database, doc: D) -> D:
    """Insert one row."""
    data = doc.model_dump()
    cols = ", ".join(f'"{c}"' for c in data)
    params = ", ".join(f"${i}" for i in range(1, len(data) + 1))
    await db.execute(
        f'INSERT INTO "{doc.collection}" ({cols}) VALUES ({params})',  # noqa: S608 - model names
        *data.values(),
    )
    return doc


async def get[D: Doc](db: Database, model: type[D], doc_id: Any) -> D | None:
    """Fetch by id."""
    return await find_one(db, model, {"_id": doc_id})


async def find_one[D: Doc](
    db: Database,
    model: type[D],
    query: Mapping[str, Any],
    sort: list[tuple[str, int]] | None = None,
) -> D | None:
    """First match (optionally sorted)."""
    rows = await find(db, model, query, sort=sort, limit=1)
    return rows[0] if rows else None


async def find[D: Doc](
    db: Database,
    model: type[D],
    query: Mapping[str, Any],
    sort: list[tuple[str, int]] | None = None,
    limit: int = 0,
) -> list[D]:
    """All matches."""
    where, args = _where(model, query)
    sql = f'SELECT * FROM "{model.collection}"{where}{_order(model, sort)}'  # noqa: S608
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [model.model_validate(dict(r)) for r in await db.fetch(sql, *args)]


async def update(db: Database, model: type[Doc], doc_id: Any, fields: Mapping[str, Any]) -> None:
    """Set columns on one row."""
    if not fields:
        return
    sets = ", ".join(f"{_col(model, f)} = ${i}" for i, f in enumerate(fields, start=2))
    await db.execute(
        f'UPDATE "{model.collection}" SET {sets} WHERE "id" = $1',  # noqa: S608
        doc_id,
        *fields.values(),
    )


async def migrate(db: Database) -> list[str]:
    """Apply app/sql/NNN_*.sql once each, in order, under an advisory lock."""
    pool = await db.pool()
    applied: list[str] = []
    async with pool.acquire() as conn:
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{db.schema}"')
        await conn.execute("SELECT pg_advisory_lock(727)")
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
            for path in sorted(SQL_DIR.glob("*.sql")):
                if path.name in done:
                    continue
                async with conn.transaction():
                    await conn.execute(path.read_text(encoding="utf-8"))
                    await conn.execute(
                        "INSERT INTO schema_migrations (name) VALUES ($1)", path.name
                    )
                applied.append(path.name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(727)")
    return applied


async def check_connectivity(settings: Settings) -> str:
    """SELECT 1; return 'ok' or raise."""
    conn = await asyncpg.connect(settings.TIGER_DATABASE_URL, timeout=5)
    try:
        await conn.fetchval("SELECT 1")
    finally:
        await conn.close()
    return "ok"
