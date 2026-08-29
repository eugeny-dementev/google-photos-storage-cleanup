from __future__ import annotations

from pathlib import Path

from gpsync.parser import Album, ItemInfo, MediaItem, parse_rpc
from gpsync.util import (
    capture_datetime,
    filename_from_content_disposition,
    format_bytes,
    parse_size,
    parse_wiz_global_data,
    sanitize_filename,
    size_allowed,
    unique_path,
    unix_seconds,
    with_album_date_prefix,
    with_file_date_prefix,
)


def test_unix_seconds_normalizes_units() -> None:
    assert unix_seconds(1_700_000_000) == 1_700_000_000
    assert unix_seconds(1_700_000_000_000) == 1_700_000_000
    assert unix_seconds(1_700_000_000_000_000) == 1_700_000_000
    assert unix_seconds(0) is None


def test_sanitize_and_unique_path(tmp_path: Path) -> None:
    assert sanitize_filename('a<>:"/\\|?*b.jpg') == "a_________b.jpg"
    first = unique_path(tmp_path, "IMG_0001.jpg")
    first.write_bytes(b"1")
    second = unique_path(tmp_path, "IMG_0001.jpg")
    assert second.name == "IMG_0001_2.jpg"


def test_content_disposition_filename() -> None:
    assert filename_from_content_disposition('attachment; filename="IMG_1234.HEIC"') == "IMG_1234.HEIC"
    assert (
        filename_from_content_disposition("attachment; filename*=UTF-8''%D1%84%D0%BE%D1%82%D0%BE.jpg")
        == "фото.jpg"
    )


def test_parse_wiz_global_data() -> None:
    html = 'head window.WIZ_global_data = {"SNlM0e":"token","oPEP7c":"a@b.c"}; more'
    data = parse_wiz_global_data(html)
    assert data["SNlM0e"] == "token"
    assert data["oPEP7c"] == "a@b.c"


def test_album_parser() -> None:
    raw = [
        "AF1album",
        ["http://thumb"],
        None,
        None,
        None,
        None,
        ["owner1"],
        {"72930366": [None, "Vacation", [None, None, None, None, 111], 12, True, "auth123"]},
    ]
    album = Album.from_data(raw)
    assert album.media_key == "AF1album"
    assert album.title == "Vacation"
    assert album.item_count == 12
    assert album.is_shared is True
    assert album.auth_key == "auth123"


def test_library_item_owned_and_shared() -> None:
    owned = ["AF1me", ["t", 10, 20], 1700000000, "dedup", 0, 1700000001, None, []]
    shared = ["AF1other", ["t", 10, 20], 1700000000, "dedup", 0, 1700000001, None, [[27]]]
    assert MediaItem.from_library_data(owned).is_owned is True
    assert MediaItem.from_library_data(shared).is_owned is False


def test_item_info_original_url() -> None:
    raw = [
        ["AF1photo", ["t", 100, 200], 1700000000, "dedup", 0, 1700000001],
        "https://lh3.googleusercontent.com/download",
        None,
        None,
        None,
        None,
        None,
        "https://lh3.googleusercontent.com/original",
    ]
    info = ItemInfo.from_data(raw)
    assert info.download_url.endswith("download")
    assert info.download_original_url.endswith("original")


def test_parse_rpc_albums_page() -> None:
    album_row = [
        "AF1album",
        ["thumb"],
        None,
        None,
        None,
        None,
        ["owner"],
        {"72930366": [None, "A", None, 3, False, None]},
    ]
    page = parse_rpc("Z5xsfc", [[album_row], "next-token"])
    assert page.items[0].title == "A"
    assert page.next_page_id == "next-token"


def test_parse_size_and_format_bytes() -> None:
    assert parse_size("50M") == 50 * 1024 * 1024
    assert parse_size("50MB") == 50 * 1024 * 1024
    assert parse_size("1.5G") == int(1.5 * 1024**3)
    assert parse_size("1024") == 1024
    assert format_bytes(50 * 1024 * 1024) == "50M"
    assert size_allowed(10, min_size=50, max_size=None) is False
    assert size_allowed(100, min_size=50, max_size=None) is True
    assert size_allowed(10, min_size=None, max_size=50) is True
    assert size_allowed(None, min_size=50, max_size=50) is True


def test_date_prefixes_use_capture_time() -> None:
    from datetime import datetime, timezone

    ts = int(datetime(2020, 7, 7, 12, 0, tzinfo=timezone.utc).timestamp())
    taken = capture_datetime(ts, 0)
    assert taken is not None
    assert with_album_date_prefix("Отпуск", taken) == "2020.07.07 Отпуск"
    assert with_file_date_prefix("IMG_1.jpg", taken) == "2020.07.07 12.00 IMG_1.jpg"
    already = "2020.07.07 12.00 IMG_1.jpg"
    assert with_file_date_prefix(already, taken) == already
    plus3 = capture_datetime(ts, 10800)
    assert plus3 is not None
    assert plus3.strftime("%H.%M") == "15.00"
