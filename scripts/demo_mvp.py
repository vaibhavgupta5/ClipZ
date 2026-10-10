"""MVP demo against a running stack (`make dev`). Phase 5 gate script.

    uv run python scripts/demo_mvp.py [--api URL] [--video fixtures/synth/speech_en.mp4]

Flow: create project -> upload -> ingest + analysis -> chat "remove silences and add yellow
word-by-word captions and make it 9:16 and denoise" -> preview -> "make captions bigger"
-> "undo" -> "export" -> download the final mp4 to ./editedVideo/demo/.
Chat steps that need Gemini are skipped (with a note) when the API reports no key; the
bare "undo"/"export" commands are routed without the model and always run.
"""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

CHAT_EDIT = "remove silences and add yellow word-by-word captions and make it 9:16 and denoise"


def log(step: str, detail: str = "") -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {step}{': ' + detail if detail else ''}", flush=True)


def wait_job(c: httpx.Client, pid: str, job_id: str, label: str, timeout_s: int = 900) -> None:
    """Follow a job's SSE stream until done (raises on failure)."""
    with c.stream("GET", f"/projects/{pid}/jobs/{job_id}/events", timeout=timeout_s) as resp:
        resp.raise_for_status()
        last = -1.0
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            data = json.loads(line[5:])
            if data["progress"] >= last + 0.2 or data["status"] in ("done", "failed"):
                last = data["progress"]
                log(label, f"{data['status']} {data['progress']:.0%}")
            if data["status"] == "failed":
                raise SystemExit(f"{label} failed: {data['error']}")
            if data["status"] == "done":
                return


def wait_timeline(c: httpx.Client, pid: str, timeout_s: int = 900) -> dict[str, Any]:
    """Poll until analysis has created timeline v1."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        resp = c.get(f"/projects/{pid}/timeline")
        if resp.status_code == 200:
            return dict(resp.json())
        time.sleep(2)
    raise SystemExit("analysis did not produce a timeline in time")


def chat(c: httpx.Client, pid: str, text: str) -> list[tuple[str, dict[str, Any]]]:
    """Send one chat message, print events, return them."""
    log("chat >", text)
    events: list[tuple[str, dict[str, Any]]] = []
    started = time.monotonic()
    first: float | None = None
    with c.stream("POST", f"/projects/{pid}/messages", json={"text": text}, timeout=600) as resp:
        resp.raise_for_status()
        name = ""
        for line in resp.iter_lines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:])
                if name == "token" and first is None:
                    first = time.monotonic() - started
                events.append((name, data))
                if name in (
                    "tool_call",
                    "preview_ready",
                    "render_ready",
                    "render_failed",
                    "error",
                    "ask",
                ):
                    log(f"  {name}", json.dumps(data)[:160])
                elif name == "done":
                    log("  reply", str(data.get("reply")))
    if first is not None:
        log("  first token", f"{first:.2f}s")
    return events


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--video", default="fixtures/synth/speech_en.mp4")
    ap.add_argument("--key", default=f"demo-{uuid.uuid4().hex[:8]}", help="X-API-Key (stub auth)")
    ap.add_argument(
        "--chat",
        action="append",
        help="custom chat turn(s) instead of the MVP script; export always runs last",
    )
    ap.add_argument("--monetized", action="store_true", help="export as monetised")
    ap.add_argument("--language", default="auto", choices=["auto", "en", "hi"])
    ap.add_argument(
        "--no-export", action="store_true", help="the chat turn itself asks for the export"
    )
    args = ap.parse_args()
    video = Path(args.video)
    if not video.exists():
        raise SystemExit(f"{video} missing: run `SPEECH=1 bash scripts/make_fixtures.sh` first")

    t0 = time.monotonic()
    with httpx.Client(base_url=args.api, headers={"X-API-Key": args.key}, timeout=60) as c:
        ready = c.get("/readyz").json()
        log("readyz", json.dumps(ready["checks"]))
        body = {"name": "MVP demo", "language": args.language}
        pid = c.post("/projects", json=body).json()["project_id"]
        log("project", pid)

        with video.open("rb") as fh:
            up = c.post(f"/projects/{pid}/uploads", files={"file": (video.name, fh, "video/mp4")})
        up.raise_for_status()
        log("uploaded", f"job {up.json()['job_id']}")
        wait_job(c, pid, up.json()["job_id"], "ingest")
        tl = wait_timeline(c, pid)
        words = tl["timeline"]["transcript"]["words"]
        log(
            "analysis",
            f"{len(words)} words, {tl['timeline']['transcript']['language']}, "
            f"{tl['timeline']['video_clips'][0]['src_out']:.1f}s; "
            f"ready after {time.monotonic() - t0:.0f}s",
        )
        log("summary", tl["summary"].replace("\n", " | "))

        turns = args.chat or [CHAT_EDIT, "make captions bigger", "undo"]
        for i, text in enumerate(turns):
            events = chat(c, pid, text)
            if any(n == "error" and "GEMINI_API_KEY" in str(d) for n, d in events):
                log("NOTE", "no GEMINI_API_KEY in the stack: skipping model-driven chat turns")
                break
            if i == 0 and not args.chat:
                assert any(n == "timeline_version_created" for n, _ in events), "no edit made"
        tl_now = c.get(f"/projects/{pid}/timeline").json()
        log("timeline", tl_now["summary"].replace("\n", " | "))
        if args.no_export:
            final = events
        else:
            export_cmd = "export" if args.monetized else "export the final video, not monetized"
            final = chat(c, pid, export_cmd)
        ready_evt = next((d for n, d in final if n == "render_ready"), None)
        if ready_evt is None:
            raise SystemExit("export did not finish within CHAT_PREVIEW_WAIT_S")
        out = Path("editedVideo/demo") / f"{pid}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        media = c.get(str(ready_evt["url"]), timeout=120)
        media.raise_for_status()
        out.write_bytes(media.content)
        log(
            "exported",
            f"{out} ({len(media.content) / 1e6:.1f} MB) in {time.monotonic() - t0:.0f}s total",
        )
        versions = c.get(f"/projects/{pid}/versions").json()
        log("versions", json.dumps(versions))
    sys.exit(0)


if __name__ == "__main__":
    main()
