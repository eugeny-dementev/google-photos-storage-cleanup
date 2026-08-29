from pathlib import Path

import pytest

from gpsync.cli import _resolve_download_album, format_status_table
from gpsync.config import Settings
from gpsync.db import Database


def test_status_table_quotes_names_and_numbers_rows() -> None:
    rows = [
        {
            "media_key": "a",
            "title": "Фото Тони",
            "is_shared": 1,
            "enumerated_at": "2026-08-29 14:22:01",
            "total": 4,
            "downloaded": 4,
            "failed": 0,
        },
        {
            "media_key": "b",
            "title": "Отпуск 2024",
            "is_shared": 0,
            "enumerated_at": None,
            "total": 312,
            "downloaded": 10,
            "failed": 2,
        },
    ]
    table = format_status_table(rows)
    assert '"Фото Тони"' in table
    assert '"Отпуск 2024"' in table
    assert "done/total" in table
    assert "4/4" in table
    assert "10/312" in table
    assert "2026-08-29 14:22" in table
    lines = table.splitlines()
    assert lines[0].startswith("  #") or lines[0].lstrip().startswith("#")
    assert "yes" in lines[2]
    assert "no" in lines[3]


def test_folder_index_confirm_yes(tmp_path: Path) -> None:
    db_path = tmp_path / "photos.db"
    with Database(db_path) as db:
        db.upsert_album("alb", "Отпуск")
        db.upsert_media("m1", file_name="a.jpg")
        db.add_album_item("alb", "m1")
        db._conn.commit()
    settings = Settings(
        data_dir=tmp_path,
        output_dir=tmp_path / "photos",
        cookies_path=tmp_path / "cookies.txt",
        db_path=db_path,
    )
    key = _resolve_download_album(settings, album=None, folder=1, assume_yes=True)
    assert key == "alb"
    with pytest.raises(SystemExit, match="No folder #9"):
        _resolve_download_album(settings, album=None, folder=9, assume_yes=True)
    with pytest.raises(SystemExit, match="either --folder"):
        _resolve_download_album(settings, album="Отпуск", folder=1, assume_yes=True)
