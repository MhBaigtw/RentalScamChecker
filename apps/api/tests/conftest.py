from pathlib import Path

import pytest


@pytest.fixture
def photo_dir(tmp_path: Path) -> Path:
    return tmp_path / "photos"
