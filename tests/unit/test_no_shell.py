"""Security invariant: no shell execution anywhere in app/ (arg lists only)."""

import pathlib


def test_no_shell_execution_in_app() -> None:
    banned = ("shell=True", "os.system(", "os.popen(", "os.spawn", "commands.getoutput")
    offenders: list[str] = []
    for path in pathlib.Path("app").rglob("*.py"):
        text = path.read_text()
        if any(token in text for token in banned):
            offenders.append(str(path))
    assert offenders == []


def test_no_ffmpeg_filter_string_concatenation() -> None:
    """Filtergraphs are built from fixed node strings; external input never concatenated."""
    import re

    offenders: list[str] = []
    for path in list(pathlib.Path("app/media").rglob("*.py")) + list(
        pathlib.Path("app/render").rglob("*.py")
    ):
        text = path.read_text()
        # f-string/plus-built -vf values from variables would show as: "-vf", f"...{var}
        for i, line in enumerate(text.splitlines(), 1):
            if re.search(r'f".*\{.*\}.*".*filter|filter.*f".*\{', line):
                offenders.append(f"{path}:{i}")
    assert offenders == []
