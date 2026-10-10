"""Caption font allowlist: resolved paths only, never user-supplied."""

from pathlib import Path

# Allowlisted fonts: (family, candidate paths). Arch and Debian (Docker) layouts differ.
_CANDIDATES: dict[str, tuple[str, tuple[str, ...]]] = {
    "latin": (
        "Liberation Sans",
        (
            "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ),
    ),
    "latin_bold": (
        "Liberation Sans",
        (
            "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ),
    ),
    "devanagari": (
        "Noto Sans Devanagari",
        (
            "/usr/share/fonts/noto/NotoSansDevanagari-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        ),
    ),
}


def _resolve(paths: tuple[str, ...]) -> str:
    return next((p for p in paths if Path(p).exists()), paths[0])


FONTS: dict[str, tuple[str, str]] = {
    name: (family, _resolve(paths)) for name, (family, paths) in _CANDIDATES.items()
}


def font_for(name: str) -> tuple[str, str]:
    """(family, path) for an allowlisted font name. Raises when missing."""
    try:
        family, path = FONTS[name]
    except KeyError:
        raise ValueError(f"unknown font {name!r}; allowlist: {sorted(FONTS)}") from None
    if not Path(path).exists():
        raise ValueError(f"allowlisted font file missing: {path}")
    return (family, path)
