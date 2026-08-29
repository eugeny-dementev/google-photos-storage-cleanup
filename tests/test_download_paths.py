from pathlib import Path

from gpsync.db import LIBRARY_ALBUM_KEY
from gpsync.download import _album_dir


def test_album_output_dirs() -> None:
    root = Path("photos")
    assert _album_dir(root, "Library", LIBRARY_ALBUM_KEY) == root / "library"
    assert _album_dir(root, "Отпуск 2024", "AF1abc") == root / "albums" / "Отпуск 2024"
    assert _album_dir(root, 'bad:name', "k") == root / "albums" / "bad_name"
    assert _album_dir(root, "Отпуск", "AF1abc", date_prefix="2024.07.15") == root / "albums" / "2024.07.15 Отпуск"
    assert _album_dir(root, "Library", LIBRARY_ALBUM_KEY, date_prefix="2024.01.02") == root / "2024.01.02 library"
