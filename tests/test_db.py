from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from gpsync.db import Database
from gpsync.timestamps import set_file_times


def test_database_tracks_pending_and_downloaded(tmp_path: Path) -> None:
    db_path = tmp_path / "photos.db"
    with Database(db_path) as db:
        db.upsert_album("alb1", "Trip", is_shared=True, auth_key="ak")
        db.upsert_media("m1", file_name="IMG_1.jpg", taken_ts=1_700_000_000, is_owned=False)
        db.add_album_item("alb1", "m1")
        db._conn.commit()

        pending = db.pending_items()
        assert len(pending) == 1
        assert pending[0]["file_name"] == "IMG_1.jpg"
        assert pending[0]["auth_key"] == "ak"

        db.mark_item(pending[0]["id"], "downloaded", local_path=str(tmp_path / "IMG_1.jpg"), bytes_written=10)
        assert db.pending_items() == []
        counts = db.status()
        assert counts.downloaded == 1
        assert counts.pending == 0

        db.add_album_item("alb1", "m1")
        db._conn.commit()
        assert db.status().pending == 0


def test_album_status_table_and_index(tmp_path: Path) -> None:
    with Database(tmp_path / "photos.db") as db:
        db.upsert_album("b", "Фото Тони", is_shared=True)
        db.upsert_album("a", "Отпуск 2024")
        db.upsert_media("m1", file_name="x.jpg", size_bytes=100)
        db.upsert_media("m2", file_name="y.jpg", size_bytes=10)
        db.add_album_item("a", "m1")
        db.add_album_item("b", "m2")
        db.mark_album_enumerated("a", 1)
        db.mark_album_enumerated("b", 1)
        db.mark_item(db.pending_items(album_key="a")[0]["id"], "downloaded", local_path="x.jpg", bytes_written=100)
        db._conn.commit()

        rows = db.list_album_status()
        assert [row["title"] for row in rows] == ["Отпуск 2024", "Фото Тони"]
        assert db.album_at_index(1)["title"] == "Отпуск 2024"
        assert db.album_at_index(2)["is_shared"] == 1
        assert db.album_at_index(1)["downloaded"] == 1
        assert db.album_at_index(1)["total"] == 1
        assert db.album_at_index(3) is None


def test_pending_items_respects_size_filters(tmp_path: Path) -> None:
    with Database(tmp_path / "photos.db") as db:
        db.upsert_album("a", "A")
        db.upsert_media("small", file_name="s.jpg", size_bytes=10)
        db.upsert_media("big", file_name="b.mp4", size_bytes=100)
        db.upsert_media("unknown", file_name="u.jpg", size_bytes=None)
        db.add_album_item("a", "small")
        db.add_album_item("a", "big")
        db.add_album_item("a", "unknown")
        db._conn.commit()
        def names(rows):
            return {row["file_name"] for row in rows}

        assert names(db.pending_items(max_size=50)) == {"s.jpg", "u.jpg"}
        assert names(db.pending_items(min_size=50)) == {"b.mp4", "u.jpg"}


def test_same_photo_in_two_albums(tmp_path: Path) -> None:
    with Database(tmp_path / "photos.db") as db:
        db.upsert_album("a", "A")
        db.upsert_album("b", "B")
        db.upsert_media("m1", file_name="x.jpg")
        db.add_album_item("a", "m1")
        db.add_album_item("b", "m1")
        db._conn.commit()
        assert len(db.pending_items()) == 2


def test_set_file_mtime(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"abc")
    taken = datetime(2020, 5, 1, 12, 0, tzinfo=timezone.utc)
    set_file_times(path, taken)
    assert int(path.stat().st_mtime) == int(taken.timestamp())
