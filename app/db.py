"""MongoDB access (PyMongo native async). One client per event loop.

Typed helpers map collections to the pydantic documents in app/models.py so the
rest of the code never touches raw dicts.
"""

import asyncio
from collections.abc import Mapping
from typing import Any

from bson.codec_options import CodecOptions
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase

from app.models import (
    AgentRun,
    Analysis,
    Asset,
    Doc,
    Job,
    Message,
    Project,
    Render,
    Timeline,
    TimelineVersion,
    User,
)
from app.settings import Settings, get_settings

Database = AsyncDatabase[dict[str, Any]]

_clients: dict[tuple[int, str], AsyncMongoClient[dict[str, Any]]] = {}


def client_for(settings: Settings) -> AsyncMongoClient[dict[str, Any]]:
    """Client bound to the running event loop (AsyncMongoClient is loop-affine)."""
    key = (id(asyncio.get_running_loop()), settings.MONGODB_URL)
    client = _clients.get(key)
    if client is None:
        client = AsyncMongoClient(
            settings.MONGODB_URL,
            uuidRepresentation="standard",
            tz_aware=True,
            serverSelectionTimeoutMS=5000,  # fail fast (readyz, workers) instead of 30 s
        )
        _clients[key] = client
    return client


def get_db(settings: Settings | None = None) -> Database:
    """Database handle for the current loop."""
    settings = settings or get_settings()
    return client_for(settings).get_database(
        settings.MONGODB_DB, codec_options=CodecOptions(uuid_representation=4, tz_aware=True)
    )


def coll(db: Database, model: type[Doc]) -> AsyncCollection[dict[str, Any]]:
    """Collection for a document class."""
    return db[model.collection]


async def insert[D: Doc](db: Database, doc: D) -> D:
    """Insert one document."""
    await coll(db, type(doc)).insert_one(doc.to_mongo())
    return doc


async def save[D: Doc](db: Database, doc: D) -> D:
    """Upsert the whole document by id."""
    await coll(db, type(doc)).replace_one({"_id": doc.id}, doc.to_mongo(), upsert=True)
    return doc


async def get[D: Doc](db: Database, model: type[D], doc_id: Any) -> D | None:
    """Fetch by id."""
    raw = await coll(db, model).find_one({"_id": doc_id})
    return model.model_validate(raw) if raw else None


async def find_one[D: Doc](
    db: Database,
    model: type[D],
    query: Mapping[str, Any],
    sort: list[tuple[str, int]] | None = None,
) -> D | None:
    """First match (optionally sorted)."""
    raw = await coll(db, model).find_one(dict(query), sort=sort)
    return model.model_validate(raw) if raw else None


async def find[D: Doc](
    db: Database,
    model: type[D],
    query: Mapping[str, Any],
    sort: list[tuple[str, int]] | None = None,
    limit: int = 0,
) -> list[D]:
    """All matches."""
    cursor = coll(db, model).find(dict(query), sort=sort, limit=limit)
    return [model.model_validate(raw) async for raw in cursor]


async def update(db: Database, model: type[Doc], doc_id: Any, fields: Mapping[str, Any]) -> None:
    """$set fields on one document."""
    await coll(db, model).update_one({"_id": doc_id}, {"$set": dict(fields)})


async def ensure_indexes(db: Database) -> None:
    """Unique + lookup indexes (idempotent; run at API/worker startup)."""
    await coll(db, User).create_index("api_key_hash", unique=True)
    await coll(db, Project).create_index("user_id")
    await coll(db, Asset).create_index([("project_id", ASCENDING), ("kind", ASCENDING)])
    await coll(db, Timeline).create_index("project_id", unique=True)
    await coll(db, TimelineVersion).create_index(
        [("timeline_id", ASCENDING), ("version", ASCENDING)], unique=True
    )
    await coll(db, Message).create_index([("project_id", ASCENDING), ("created_at", ASCENDING)])
    await coll(db, Job).create_index("idempotency_key", unique=True)
    await coll(db, Render).create_index("project_id")
    await coll(db, Analysis).create_index(
        [
            ("source_sha", ASCENDING),
            ("analyzer", ASCENDING),
            ("version", ASCENDING),
            ("params_hash", ASCENDING),
        ],
        unique=True,
    )
    await coll(db, Analysis).create_index([("project_id", ASCENDING), ("created_at", DESCENDING)])
    await coll(db, AgentRun).create_index("project_id")


async def check_connectivity(settings: Settings) -> str:
    """Ping the server; return 'ok' or raise."""
    await client_for(settings).admin.command("ping")
    return "ok"
