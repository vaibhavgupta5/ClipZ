"""ffmpeg runner: progress parsing must not deadlock on a chatty stderr."""

import sys

import pytest

from app.render.errors import RenderError
from app.render.render import _run_with_progress

CHATTY = (
    "import sys\n"
    "sys.stderr.write('libass: event\\n' * 150000)\n"  # ~2 MB, far beyond a pipe buffer
    "print('out_time_ms=1000000', flush=True)\n"
    "print('progress=end', flush=True)\n"
)


def test_chatty_stderr_does_not_deadlock() -> None:
    seen: list[float] = []
    _run_with_progress(
        [sys.executable, "-c", CHATTY, "out"], 2.0, seen.append, 0.0, 1.0, timeout_s=20
    )
    assert seen == [0.5]


def test_failure_reports_stderr_tail_and_timeout() -> None:
    fail = "import sys; sys.stderr.write('boom happened'); sys.exit(3)"
    with pytest.raises(RenderError, match="boom happened"):
        _run_with_progress([sys.executable, "-c", fail, "out"], 1.0, lambda f: None, 0.0, 1.0)
    hang = "import time; time.sleep(30)"
    with pytest.raises(RenderError, match="timed out"):
        _run_with_progress(
            [sys.executable, "-c", hang, "out"], 1.0, lambda f: None, 0.0, 1.0, timeout_s=1
        )
