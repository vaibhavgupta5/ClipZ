"""Birthday-vlog demo: a hand-written edit plan applied through the production path.

    PYTHONPATH=. uv run python scripts/demo_birthday.py PROJECT_ID [--api URL]

The plan below is what the Director would send for the prompt in PROMPT. It is
written by hand (not by Gemini; the free-tier quota was exhausted when this was
recorded), but it goes through exactly the same code as a live chat turn: the
Director loop, executor validation, deterministic placement rules, versioning in
Tiger Data, the render queue and the worker. Needs `make dev` running and an
analysed project (upload first).
"""

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

from app import db as db_ops
from app.agent.context import project_data, system_prompt
from app.agent.director import Event, run_director
from app.agent.workspace import DbWorkspace, latest_bundle
from app.models import Project
from app.models import Timeline as TimelineRow
from app.providers.fakes import ScriptedToolLLM
from app.providers.tools import ToolCall, ToolTurnReply
from app.settings import get_settings
from app.storage import get_storage

PROMPT = (
    "This is Shivang bhai's birthday party vlog. Make it a fun, high-energy reel: cut the "
    "dead air and filler words, clean up the background noise, add happy upbeat background "
    "music, bold word-by-word captions that pop, and drop in celebration reaction GIFs, a "
    "party image and one funny birthday meme at the best moments."
)

PLAN: list[tuple[str, dict[str, object]]] = [
    ("remove_silence", {}),
    ("remove_fillers", {"lang": "auto"}),
    ("denoise", {}),
    ("add_music", {"genre": "upbeat", "mood": "happy party celebration", "level_db": -18}),
    (
        "add_captions",
        {
            "mode": "word_highlight",
            "position": "center_lower",
            "words_per_line": 2,
            "size_px": 96,
            "uppercase": True,
            "animation": "pop",
            "highlight_color": "#FFD400",
        },
    ),
    (
        "add_inserts",
        {
            "items": [
                {"at_word": "w4", "kind": "gif", "query": "party celebration"},  # "starting"
                {"at_word": "w10", "kind": "gif", "query": "eyes look"},  # "what are you doing?"
                {"at_word": "w18", "kind": "image", "query": "birthday candles"},  # "done a lot"
                {
                    "at_word": "w22",  # first "Shivang bhai's birthday party"
                    "kind": "meme",
                    "query": "one does not simply",
                    "top_text": "One does not simply",
                    "bottom_text": "skip Shivang bhai's party",
                },
                {"at_word": "w30", "kind": "gif", "query": "fire hype"},  # the chant
            ]
        },
    ),
    ("export", {"monetized": False}),  # the meme template is personal-use only
]


async def apply_plan(project_id: uuid.UUID) -> list[str]:
    """Run the plan through the Director; returns render ids."""
    settings = get_settings()
    db = db_ops.get_db(settings)
    project = await db_ops.get(db, Project, project_id)
    row = await db_ops.find_one(db, TimelineRow, {"project_id": project_id})
    if project is None or row is None:
        raise SystemExit("project or timeline not found (upload + wait for analysis first)")
    bundle = await latest_bundle(db, get_storage(settings), project_id)
    ws = DbWorkspace(db, project_id, row, bundle)
    state = await ws.state()
    system = system_prompt(
        project_data(
            state.timeline,
            list(bundle.speech if bundle else []),
            list(bundle.fillers if bundle else []),
        )
    )
    script = [
        ToolTurnReply(
            calls=[ToolCall(id=f"plan{i}", name=n, args=a) for i, (n, a) in enumerate(PLAN)],
            stop_reason="tool_use",
        ),
        ToolTurnReply(
            text="Birthday reel ready: tighter cut, clean audio, upbeat music, "
            "pop captions and inserts."
        ),
    ]

    def emit(e: Event) -> None:
        if e.type == "tool_result":
            d = e.data
            detail = d.get("result") if d.get("ok") else d.get("error")
            print(f"  {'OK ' if d.get('ok') else 'ERR'} {d['name']}: {json.dumps(detail)[:220]}")
        elif e.type in ("preview_queued", "render_queued"):
            print(f"  {e.type}: {e.data['render_id']}")

    print(
        f"prompt: {PROMPT}\n"
        "plan: hand-written (see scripts/demo_birthday.py), applied via the Director"
    )
    result = await run_director(ScriptedToolLLM(script), ws, system, PROMPT, emit=emit)
    print(f"reply: {result.reply}")
    await db.close()
    return result.renders


def wait_and_download(api: str, key: str, project_id: str, render_ids: list[str]) -> None:
    """Poll renders through the API and save the final export."""
    with httpx.Client(base_url=api, headers={"X-API-Key": key}, timeout=60) as c:
        for rid in render_ids:
            for _ in range(600):
                r = c.get(f"/projects/{project_id}/renders/{rid}")
                if r.status_code == 200 and r.json()["status"] in ("done", "failed"):
                    break
                time.sleep(1)
            info = r.json()
            print(f"render {info['profile']}: {info['status']} {info.get('output_url')}")
            if info["profile"] == "final" and info["status"] == "done":
                out = Path("editedVideo/demo/birthday_reel.mp4")
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(c.get(info["output_url"], timeout=120).content)
                print(f"saved {out} ({out.stat().st_size / 1e6:.1f} MB)")
                for credit in info.get("attributions") or []:
                    print(f"credit: {credit}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("project_id")
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--key", default="birthday-demo", help="X-API-Key that owns the project")
    args = ap.parse_args()
    renders = asyncio.run(apply_plan(uuid.UUID(args.project_id)))
    wait_and_download(args.api, args.key, args.project_id, renders)
    sys.exit(0)


if __name__ == "__main__":
    main()
