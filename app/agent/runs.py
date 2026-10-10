"""Agent run logging: prompt hash, tools, latency, tokens and estimated cost (Phase 5 T5).

Prices are USD per 1M tokens from https://ai.google.dev/gemini-api/docs/pricing
(retrieved 2026-10-10), paid tier, keyed by model and the date they take effect.
Unknown models log a cost of 0 rather than a guess.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from app import db as db_ops
from app.agent.director import DirectorResult
from app.db import Database
from app.models import AgentRun
from app.providers.tools import Usage


@dataclass(frozen=True)
class Price:
    """USD per 1M tokens."""

    input: float
    output: float
    cached: float


_FLASH_2026 = Price(input=0.75, output=3.75, cached=0.075)
_FLASH_2027 = Price(input=1.50, output=7.50, cached=0.15)

# model -> [(effective_from, price)], newest last
PRICES: dict[str, list[tuple[date, Price]]] = {
    "gemini-3.8-flash": [(date(2000, 1, 1), _FLASH_2026), (date(2027, 1, 1), _FLASH_2027)],
    "gemini-3.6-flash": [(date(2000, 1, 1), _FLASH_2026), (date(2027, 1, 1), _FLASH_2027)],
    "gemini-3.5-flash-lite": [(date(2000, 1, 1), Price(input=0.30, output=2.50, cached=0.03))],
    "gemini-3.1-flash-lite": [(date(2000, 1, 1), Price(input=0.25, output=1.50, cached=0.025))],
    "gemini-3.1-pro-preview": [(date(2000, 1, 1), Price(input=2.00, output=12.00, cached=0.20))],
}


def price_for(model: str, on: date) -> Price | None:
    """Price in effect for ``model`` on ``on`` (None when unknown)."""
    tiers = PRICES.get(model)
    if not tiers:
        return None
    current = [p for start, p in tiers if start <= on]
    return current[-1] if current else None


def cost_usd(model: str, usage: Usage, on: date | None = None) -> float:
    """Estimated cost. Cached input is billed at the cache rate; thinking at the output rate.

    Assumes total_input_tokens includes cached tokens and total_output_tokens excludes
    thought tokens (ADR-013; verify against a live key).
    """
    price = price_for(model, on or datetime.now(UTC).date())
    if price is None:
        return 0.0
    fresh = max(0, usage.input_tokens - usage.cache_read_tokens)
    total = (
        fresh * price.input
        + usage.cache_read_tokens * price.cached
        + (usage.output_tokens + usage.thought_tokens) * price.output
    )
    return round(total / 1_000_000, 6)


def prompt_hash(system: str) -> str:
    """16-hex hash of the system prompt (groups runs by prompt version)."""
    return hashlib.sha256(system.encode()).hexdigest()[:16]


async def log_run(
    db: Database,
    project_id: uuid.UUID,
    system: str,
    result: DirectorResult,
    latency_ms: int,
    first_token_ms: int | None,
) -> AgentRun:
    """Persist one Director run."""
    return await db_ops.insert(
        db,
        AgentRun(
            project_id=project_id,
            model=result.model,
            prompt_hash=prompt_hash(system),
            tools=result.tools,
            stopped=result.stopped,
            iterations=result.iterations,
            latency_ms=latency_ms,
            first_token_ms=first_token_ms,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens + result.usage.thought_tokens,
            cache_read_tokens=result.usage.cache_read_tokens,
            cost_usd=cost_usd(result.model, result.usage),
        ),
    )
