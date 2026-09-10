from pathlib import Path

import pytest

from gpsync.cli import _resolve_download_albums, format_status_table
from gpsync.config import Settings
from gpsync.db import LIBRARY_ALBUM_KEY, Database


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
    assert "ignore" in table
    assert "4/4" in table
    assert "10/312" in table
    assert "2026-08-29 14:22" in table
    lines = table.splitlines()
    assert lines[0].startswith("  #") or lines[0].lstrip().startswith("#")
    assert "yes" in lines[2]
    assert "no" in lines[3]


def test_status_table_library_is_folder_zero() -> None:
    rows = [
        {
            "media_key": LIBRARY_ALBUM_KEY,
            "title": "Library",
            "is_shared": 0,
            "enumerated_at": "2026-08-29 14:22:01",
            "total": 10,
            "downloaded": 1,
            "failed": 0,
            "ignored": 0,
        },
        {
            "media_key": "a",
            "title": "Отпуск",
            "is_shared": 0,
            "enumerated_at": None,
            "total": 2,
            "downloaded": 0,
            "failed": 0,
            "ignored": 0,
        },
    ]
    table = format_status_table(rows)
    lines = table.splitlines()
    assert lines[2].lstrip().startswith("0")
    assert '"Library"' in lines[2]
    assert lines[3].lstrip().startswith("1")
    assert '"Отпуск"' in lines[3]


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
    key = _resolve_download_albums(settings, album=None, folder=[1], assume_yes=True)
    assert key.keys == ["alb"]
    assert key.exclude_keys == []
    default = _resolve_download_albums(settings, album=None, folder=None, assume_yes=True)
    assert default.keys is None
    assert default.exclude_keys == [LIBRARY_ALBUM_KEY]
    with pytest.raises(SystemExit, match="No folder #9"):
        _resolve_download_albums(settings, album=None, folder=[9], assume_yes=True)
    with pytest.raises(SystemExit, match="either --folder"):
        _resolve_download_albums(settings, album="Отпуск", folder=[1], assume_yes=True)


def test_folder_spec_selects_several_albums(tmp_path: Path) -> None:
    db_path = tmp_path / "photos.db"
    with Database(db_path) as db:
        db.upsert_album("a", "Alpha")
        db.upsert_album("b", "Beta")
        db.upsert_album("c", "Gamma")
        db.upsert_media("m1", file_name="a.jpg")
        db.upsert_media("m2", file_name="b.jpg")
        db.upsert_media("m3", file_name="c.jpg")
        db.add_album_item("a", "m1")
        db.add_album_item("b", "m2")
        db.add_album_item("c", "m3")
        db._conn.commit()
    settings = Settings(
        data_dir=tmp_path,
        output_dir=tmp_path / "photos",
        cookies_path=tmp_path / "cookies.txt",
        db_path=db_path,
    )
    keys = _resolve_download_albums(settings, album=None, folder=[1, 3], assume_yes=True)
    assert keys.keys == ["a", "c"]
    assert keys.exclude_keys == []


def test_ignore_folder_excludes_this_run(tmp_path: Path) -> None:
    db_path = tmp_path / "photos.db"
    with Database(db_path) as db:
        db.upsert_album("a", "Alpha")
        db.upsert_album("b", "Beta")
        db.upsert_album("c", "Gamma")
        db.upsert_media("m1", file_name="a.jpg")
        db.upsert_media("m2", file_name="b.jpg")
        db.upsert_media("m3", file_name="c.jpg")
        db.add_album_item("a", "m1")
        db.add_album_item("b", "m2")
        db.add_album_item("c", "m3")
        db._conn.commit()
    settings = Settings(
        data_dir=tmp_path,
        output_dir=tmp_path / "photos",
        cookies_path=tmp_path / "cookies.txt",
        db_path=db_path,
    )
    skipped = _resolve_download_albums(
        settings, album=None, folder=None, ignore_folder=[1, 3], assume_yes=True
    )
    assert skipped.keys is None
    assert skipped.exclude_keys == [LIBRARY_ALBUM_KEY, "a", "c"]
    mixed = _resolve_download_albums(
        settings, album=None, folder=[1, 2, 3], ignore_folder=[2], assume_yes=True
    )
    assert mixed.keys == ["a", "c"]
    assert mixed.exclude_keys == []
    with pytest.raises(SystemExit, match="Nothing left"):
        _resolve_download_albums(
            settings, album=None, folder=[2], ignore_folder=[2], assume_yes=True
        )
    with pytest.raises(SystemExit, match="either --folder"):
        _resolve_download_albums(
            settings, album="Alpha", folder=None, ignore_folder=[1], assume_yes=True
        )


def test_ignore_unignore_cli(tmp_path: Path) -> None:
    from gpsync.cli import main

    db_path = tmp_path / "photos.db"
    with Database(db_path) as db:
        db.upsert_album("a", "Alpha")
        db.upsert_album("b", "Beta")
        db._conn.commit()
    argv = ["--data-dir", str(tmp_path), "--output-dir", str(tmp_path / "out")]
    assert main([*argv, "ignore", "-y", "--folder", "1"]) == 0
    with Database(db_path) as db:
        assert db.get_album("a")["ignored"] == 1
        assert db.get_album("b")["ignored"] == 0
    assert main([*argv, "unignore", "-y", "--all"]) == 0
    with Database(db_path) as db:
        assert db.get_album("a")["ignored"] == 0


def test_library_folder_zero_and_download_flag(tmp_path: Path) -> None:
    db_path = tmp_path / "photos.db"
    with Database(db_path) as db:
        db.upsert_album(LIBRARY_ALBUM_KEY, "Library")
        db.upsert_album("a", "Alpha")
        db.upsert_media("m1", file_name="a.jpg")
        db.add_album_item("a", "m1")
        db._conn.commit()
    settings = Settings(
        data_dir=tmp_path,
        output_dir=tmp_path / "photos",
        cookies_path=tmp_path / "cookies.txt",
        db_path=db_path,
    )
    assert _resolve_download_albums(
        settings, album=None, folder=[0], assume_yes=True
    ).keys == [LIBRARY_ALBUM_KEY]
    assert _resolve_download_albums(
        settings, album=None, folder=[1], assume_yes=True
    ).keys == ["a"]
    lib = _resolve_download_albums(
        settings, album=None, folder=None, include_library=True, assume_yes=True
    )
    assert lib.keys == [LIBRARY_ALBUM_KEY]
    both = _resolve_download_albums(
        settings, album=None, folder=[1], include_library=True, assume_yes=True
    )
    assert both.keys == [LIBRARY_ALBUM_KEY, "a"]
    empty = tmp_path / "nolib.db"
    with Database(empty) as db:
        db.upsert_album("only", "Only")
        db._conn.commit()
    nolib = Settings(
        data_dir=tmp_path,
        output_dir=tmp_path / "photos",
        cookies_path=tmp_path / "cookies.txt",
        db_path=empty,
    )
    with pytest.raises(SystemExit, match="No folder #0"):
        _resolve_download_albums(nolib, album=None, folder=[0], assume_yes=True)
    with pytest.raises(SystemExit, match="enumerate --library"):
        _resolve_download_albums(nolib, album=None, folder=None, include_library=True, assume_yes=True)
