"""Director evals. CI: scripted ideal calls (machinery). Live: real Gemini (`make eval-live`)."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.agent.context import project_data, system_prompt
from app.agent.director import run_director
from app.providers.fakes import ScriptedToolLLM
from tests.evals.cases import CASES, Case, workspace

LIVE_PASS_RATE = 0.85


async def _run(case: Case, llm: object) -> list[str]:
    ws = workspace(case.words, case.lang)
    tl = ws.timeline()
    system = system_prompt(project_data(tl, ws.speech, ws.fillers))
    result = await run_director(llm, ws, system, case.message)  # type: ignore[arg-type]
    return [reason for check in case.expect if (reason := check(result, ws)) is not None]


def test_thirty_cases() -> None:
    assert len(CASES) >= 30 and len({c.id for c in CASES}) == len(CASES)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_scripted_ideal_passes(case: Case) -> None:
    failures = await _run(case, ScriptedToolLLM(list(case.ideal)))
    assert failures == [], failures


@pytest.mark.integration
async def test_live_gemini_pass_rate() -> None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        pytest.skip("GEMINI_API_KEY not set")
    from app.providers.gemini import GeminiLLM
    from app.settings import get_settings

    settings = get_settings()
    llm = GeminiLLM(key, settings.GEMINI_MODEL, settings.GEMINI_THINKING_LEVEL)
    results: dict[str, list[str]] = {}
    for case in CASES:
        try:
            results[case.id] = await _run(case, llm)
        except Exception as exc:  # noqa: BLE001 - a crash is a failed case, keep scoring
            results[case.id] = [f"{type(exc).__name__}: {exc}"]
    passed = sum(1 for r in results.values() if not r)
    rate = passed / len(CASES)
    out = Path("tests/evals/results")
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (out / f"live-{stamp}.json").write_text(
        json.dumps(
            {
                "model": settings.GEMINI_MODEL,
                "pass_rate": rate,
                "failures": {k: v for k, v in results.items() if v},
            },
            indent=2,
        )
    )
    assert rate >= LIVE_PASS_RATE, (
        f"{passed}/{len(CASES)} passed: {[k for k, v in results.items() if v]}"
    )
