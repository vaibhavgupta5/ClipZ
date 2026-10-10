"""Run cost math: dated Gemini prices, cache discount, unknown models cost 0."""

from datetime import date

import pytest

from app.agent.runs import cost_usd, price_for, prompt_hash
from app.providers.tools import Usage


def test_flash_price_changes_on_2027_01_01() -> None:
    assert price_for("gemini-3.8-flash", date(2026, 12, 31)).input == 0.75  # type: ignore[union-attr]
    assert price_for("gemini-3.8-flash", date(2027, 1, 1)).input == 1.50  # type: ignore[union-attr]
    assert price_for("some-other-model", date(2026, 10, 10)) is None


def test_cost_bills_cached_input_at_cache_rate_and_thinking_as_output() -> None:
    usage = Usage(
        input_tokens=1_000_000,
        cache_read_tokens=400_000,
        output_tokens=100_000,
        thought_tokens=100_000,
    )
    # 600k fresh * 0.75 + 400k cached * 0.075 + 200k out * 3.75 (per 1M)
    assert cost_usd("gemini-3.8-flash", usage, date(2026, 10, 10)) == pytest.approx(
        0.45 + 0.03 + 0.75
    )
    assert cost_usd("unknown", usage, date(2026, 10, 10)) == 0.0


def test_prompt_hash_is_stable() -> None:
    assert prompt_hash("a") == prompt_hash("a") != prompt_hash("b")
