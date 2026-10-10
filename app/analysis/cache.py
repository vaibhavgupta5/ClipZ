"""Analysis bundle cache: storage JSON indexed by the analyses collection."""

import hashlib
import json
import uuid

from pymongo.errors import DuplicateKeyError

from app import db as db_ops
from app.analysis.models import AnalysisBundle, cache_key
from app.db import Database
from app.models import Analysis
from app.storage import Storage


def params_hash(params: dict[str, object]) -> str:
    """12-char hash of sorted params (matches models.cache_key)."""
    return hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]


async def load_cached(
    db: Database,
    store: Storage,
    project_id: uuid.UUID,
    source_sha: str,
    analyzer: str,
    version: str,
    params: dict[str, object],
) -> AnalysisBundle | None:
    """Cache hit -> validated bundle; miss (or lost blob) -> None."""
    row = await db_ops.find_one(
        db,
        Analysis,
        {
            "source_sha": source_sha,
            "analyzer": analyzer,
            "version": version,
            "params_hash": params_hash(params),
        },
    )
    if row is None:
        return None
    try:
        raw = store.get_bytes(row.bundle_key)
    except KeyError:
        return None
    return AnalysisBundle.model_validate(json.loads(raw))


async def save_bundle(
    db: Database,
    store: Storage,
    project_id: uuid.UUID,
    source_sha: str,
    analyzer: str,
    version: str,
    params: dict[str, object],
    bundle: AnalysisBundle,
) -> str:
    """Persist bundle JSON + index row (idempotent on conflict). Returns the key."""
    key = cache_key(source_sha, analyzer, version, params)
    store.put_bytes(key, bundle.model_dump_json().encode(), "application/json")
    try:
        await db_ops.insert(
            db,
            Analysis(
                project_id=project_id,
                source_sha=source_sha,
                analyzer=analyzer,
                version=version,
                params_hash=params_hash(params),
                bundle_key=key,
            ),
        )
    except DuplicateKeyError:
        pass  # another run cached the same bundle
    return key
