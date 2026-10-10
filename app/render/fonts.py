"""Caption font allowlist: resolved paths only, never user-supplied."""

from pathlib import Path

FONTS: dict[str, tuple[str, str]] = {
    "latin": ("Liberation Sans", "/usr/share/fonts/liberation/LiberationSans-Regular.ttf"),
    "devanagari": ("Noto Sans Devanagari", "/usr/share/fonts/noto/NotoSansDevanagari-Regular.ttf"),
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
