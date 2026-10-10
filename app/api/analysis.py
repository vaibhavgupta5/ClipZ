"""Analysis endpoints: cached bundle + LLM-format transcript."""

import json
import uuid

from fastapi import APIRouter, HTTPException

from app import db as db_ops
from app.analysis.llm_stages import transcript_lines
from app.api.deps import DbDep, UserDep, owned_project
from app.db import Database
from app.models import Analysis
from app.settings import get_settings
from app.storage import get_storage
from app.timeline.transcript import Transcript

router = APIRouter()


async def _latest_bundle(
    db: Database,
    project_id: uuid.UUID,
) -> tuple[Analysis, dict[str, object]]:
    row = await db_ops.find_one(db, Analysis, {"project_id": project_id}, sort=[("created_at", -1)])
    if row is None:
        raise HTTPException(status_code=404, detail="no analysis yet for this project")
    store = get_storage(get_settings())
    try:
        raw = store.get_bytes(row.bundle_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="analysis blob missing") from None
    return row, json.loads(raw)


@router.get("/projects/{project_id}/analysis")
async def get_analysis(project_id: uuid.UUID, db: DbDep, user_id: UserDep) -> dict[str, object]:
    """Latest cached AnalysisBundle for the project."""
    await owned_project(db, user_id, project_id)
    row, bundle = await _latest_bundle(db, project_id)
    return {"bundle": bundle, "analyzer": row.analyzer, "version": row.version}


@router.get("/projects/{project_id}/transcript")
async def get_transcript(project_id: uuid.UUID, db: DbDep, user_id: UserDep) -> dict[str, object]:
    """LLM-format transcript ([wID] text) + beats section."""
    await owned_project(db, user_id, project_id)
    _, bundle = await _latest_bundle(db, project_id)
    transcript = Transcript.model_validate(bundle["transcript"])
    lines = transcript_lines(transcript)
    raw_beats = bundle.get("beats", [])
    beats = raw_beats if isinstance(raw_beats, list) else []
    if beats:
        lines += "\n\nbeats:\n" + "\n".join(
            f"{b['id']}: {b['intent']} [{','.join(b['word_ids'])}] :: {b.get('query_hint', '')}"
            for b in beats
        )
    return {"transcript": lines, "language": bundle.get("language", "auto")}
