"""Test-wide isolation: no test may write into the real ./upload, ./work or ./editedVideo.

Render/golden tests call get_storage(get_settings()); without this they wrote test
renders (colour bars + 'a b c' captions) into the user's editedVideo folder.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.settings import get_settings


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for var, sub in (("UPLOAD_DIR", "upload"), ("WORK_DIR", "work"), ("EDITED_DIR", "edited")):
        monkeypatch.setenv(var, str(tmp_path / "storage" / sub))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
