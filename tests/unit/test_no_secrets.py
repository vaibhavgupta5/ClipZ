"""No credentials in tracked files (keys live only in the git-ignored .env)."""

import re
import subprocess

PATTERNS = {
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    "elevenlabs_key": re.compile(r"sk_[0-9a-f]{40,}"),
    "postgres_password_url": re.compile(
        r"postgres(?:ql)?://[^:\s]+:(?!reelforge@|y@)[^@\s]{6,}@[^\s]*tsdb"
    ),
}


def test_no_secrets_in_tracked_files() -> None:
    files = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,  # noqa: S607
    ).stdout.split()
    hits = []
    for path in files:
        try:
            text = open(path, encoding="utf-8").read()
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        hits += [f"{path}: {name}" for name, rx in PATTERNS.items() if rx.search(text)]
    assert hits == [], hits
