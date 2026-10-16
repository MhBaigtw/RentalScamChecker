from pathlib import Path

import pytest

from rentalscamchecker.storage import LocalDiskStorage

KEY = "checks/0b6f2c1e-1111-4222-8333-944455556666/7c1d.jpg"


def test_put_get_delete_round_trip(tmp_path: Path) -> None:
    storage = LocalDiskStorage(tmp_path)

    storage.put(KEY, b"jpeg-bytes")
    assert storage.get(KEY) == b"jpeg-bytes"
    assert (tmp_path / KEY).is_file()

    storage.delete(KEY)
    assert not (tmp_path / KEY).exists()


def test_deleting_a_missing_key_is_not_an_error(tmp_path: Path) -> None:
    LocalDiskStorage(tmp_path).delete(KEY)


@pytest.mark.parametrize(
    "key", ["../escape.jpg", "checks/../../escape.jpg", "/abs/path.jpg", "C:/x.jpg", "a\\b.jpg", ""]
)
def test_keys_that_could_escape_the_root_are_refused(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError, match="invalid storage key"):
        LocalDiskStorage(tmp_path).put(key, b"x")
