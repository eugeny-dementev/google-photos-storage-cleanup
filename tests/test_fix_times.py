from datetime import datetime, timezone
from pathlib import Path
import os

from gpsync.config import Settings
from gpsync.db import Database
from gpsync.download import fix_downloaded_times


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "photos",
        cookies_path=tmp_path / "data" / "cookies.txt",
        db_path=tmp_path / "data" / "photos.db",
    )


def test_fix_time_uses_stored_path_including_date_prefix(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    taken = datetime(2020, 5, 1, 12, 0, tzinfo=timezone.utc)
    prefixed = settings.output_dir / "albums" / "2020.05.01 Trip" / "2020.05.01 12.00 IMG.jpg"
    prefixed.parent.mkdir(parents=True)
    prefixed.write_bytes(b"jpeg")
    os.utime(prefixed, (datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp(),) * 2)

    with Database(settings.db_path) as db:
        db.upsert_album("alb", "Trip")
        db.upsert_media("m1", file_name="IMG.jpg", taken_ts=int(taken.timestamp()), timezone_offset=0)
        db.add_album_item("alb", "m1")
        item_id = db.pending_items(album_key="alb")[0]["id"]
        db.mark_item(item_id, "downloaded", local_path=str(prefixed), bytes_written=4)

    assert int(prefixed.stat().st_mtime) != int(taken.timestamp())
    assert fix_downloaded_times(settings) == 0
    assert int(prefixed.stat().st_mtime) == int(taken.timestamp())


def test_fix_time_honors_folder_and_skips_other_albums(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_dirs()
    taken = datetime(2021, 6, 2, 8, 0, tzinfo=timezone.utc)
    keep = datetime(2024, 2, 2, tzinfo=timezone.utc).timestamp()
    path_a = settings.output_dir / "a.jpg"
    path_b = settings.output_dir / "b.jpg"
    path_a.write_bytes(b"a")
    path_b.write_bytes(b"b")
    os.utime(path_a, (keep, keep))
    os.utime(path_b, (keep, keep))

    with Database(settings.db_path) as db:
        db.upsert_album("alpha", "Alpha")
        db.upsert_album("beta", "Beta")
        db.upsert_media("m1", file_name="a.jpg", taken_ts=int(taken.timestamp()))
        db.upsert_media("m2", file_name="b.jpg", taken_ts=int(taken.timestamp()))
        db.add_album_item("alpha", "m1")
        db.add_album_item("beta", "m2")
        db.mark_item(db.pending_items(album_key="alpha")[0]["id"], "downloaded", local_path=str(path_a))
        db.mark_item(db.pending_items(album_key="beta")[0]["id"], "downloaded", local_path=str(path_b))

    assert fix_downloaded_times(settings, album_keys=["alpha"]) == 0
    assert int(path_a.stat().st_mtime) == int(taken.timestamp())
    assert int(path_b.stat().st_mtime) == int(keep)
