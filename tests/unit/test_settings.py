"""Settings tests: defaults and .env.example parity."""

from pathlib import Path


def _env_example_keys() -> set[str]:
    text = Path(".env.example").read_text()
    keys: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, _ = line.partition("=")
        keys.add(key.strip())
    return keys


def test_max_video_seconds_default_is_90() -> None:
    from app.settings import get_settings

    assert get_settings().MAX_VIDEO_SECONDS == 90


def test_default_caption_script_is_devanagari() -> None:
    from app.settings import get_settings

    assert get_settings().DEFAULT_CAPTION_SCRIPT == "devanagari"


def test_every_setting_documented_in_env_example() -> None:
    from app.settings import Settings

    assert set(Settings.model_fields) == _env_example_keys()
