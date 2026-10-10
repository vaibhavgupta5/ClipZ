"""Telemetry read API over the Tiger Data continuous aggregates (ADR-016).

/metrics/overview reads only the hourly rollups (cheap at any data volume);
/projects/{id}/metrics reads one project's raw run log via its time index.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import DbDep, UserDep, owned_project

router = APIRouter()

_OVERVIEW: dict[str, str] = {
    "agent_hourly": """
        SELECT bucket, model, runs, avg_latency_ms, p95_latency_ms, avg_first_token_ms,
               input_tokens, output_tokens, cache_read_tokens, cost_usd, answered, asked, failed
        FROM agent_runs_hourly WHERE bucket > now() - make_interval(hours => $1)
        ORDER BY bucket""",
    "tools": """
        SELECT tool, sum(calls)::bigint AS calls, sum(errors)::bigint AS errors,
               round((sum(errors)::numeric / NULLIF(sum(calls), 0)), 4)::float AS error_rate,
               avg(avg_latency_ms) AS avg_latency_ms
        FROM tool_calls_hourly WHERE bucket > now() - make_interval(hours => $1)
        GROUP BY tool ORDER BY calls DESC""",
    "stages": """
        SELECT stage, sum(runs)::bigint AS runs, sum(failures)::bigint AS failures,
               avg(avg_ms) AS avg_ms, max(p95_ms) AS p95_ms,
               sum(avg_ms * runs) / 1000.0 / NULLIF(sum(media_s), 0) AS realtime_factor
        FROM pipeline_stages_hourly WHERE bucket > now() - make_interval(hours => $1)
        GROUP BY stage ORDER BY stage""",
    "totals": """
        SELECT coalesce(sum(runs), 0)::bigint AS runs, coalesce(sum(cost_usd), 0) AS cost_usd,
               coalesce(sum(input_tokens), 0)::bigint AS input_tokens,
               coalesce(sum(cache_read_tokens), 0)::bigint AS cache_read_tokens
        FROM agent_runs_hourly WHERE bucket > now() - make_interval(hours => $1)""",
}


def _rows(records: list[Any]) -> list[dict[str, Any]]:
    out = []
    for r in records:
        row = dict(r)
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
        out.append(row)
    return out


@router.get("/metrics/overview")
async def overview(
    db: DbDep, _user: UserDep, hours: int = Query(24, ge=1, le=24 * 90)
) -> dict[str, object]:
    """Agent latency/cost/outcomes, tool error rates and pipeline speed for the last N hours."""
    result: dict[str, object] = {"hours": hours}
    for name, sql in _OVERVIEW.items():
        result[name] = _rows(await db.fetch(sql, hours))
    return result


@router.get("/projects/{project_id}/metrics")
async def project_metrics(
    project_id: uuid.UUID, db: DbDep, user_id: UserDep, limit: int = Query(50, ge=1, le=500)
) -> dict[str, object]:
    """This project's recent Director runs with their tool calls."""
    await owned_project(db, user_id, project_id)
    runs = await db.fetch(
        """SELECT r.id, r.created_at, r.model, r.stopped, r.iterations, r.latency_ms,
                  r.first_token_ms, r.input_tokens, r.output_tokens, r.cost_usd,
                  coalesce(json_agg(json_build_object(
                      'tool', t.tool, 'ok', t.ok, 'error_code', t.error_code,
                      'latency_ms', t.latency_ms) ORDER BY t.created_at)
                    FILTER (WHERE t.id IS NOT NULL), '[]') AS tool_calls
           FROM agent_runs r
           LEFT JOIN tool_calls t ON t.run_id = r.id
               AND t.created_at BETWEEN r.created_at - interval '1 minute'
                                    AND r.created_at + interval '1 hour'
           WHERE r.project_id = $1
           GROUP BY r.id, r.created_at, r.model, r.stopped, r.iterations, r.latency_ms,
                    r.first_token_ms, r.input_tokens, r.output_tokens, r.cost_usd
           ORDER BY r.created_at DESC LIMIT $2""",
        project_id,
        limit,
    )
    return {"runs": _rows(runs)}
