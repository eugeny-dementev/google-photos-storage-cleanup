from pathlib import Path

from gpsync.auth import (
    _is_google_domain,
    clear_session_secrets,
    import_cookies_txt,
    write_netscape_cookies,
)
from gpsync.config import Settings


def test_google_domain_filter() -> None:
    assert _is_google_domain(".google.com")
    assert _is_google_domain("photos.google.com")
    assert _is_google_domain(".google.ru")
    assert _is_google_domain("lh3.googleusercontent.com")
    assert not _is_google_domain("evil.example.com")
    assert not _is_google_domain("notgoogle.com")


def test_write_netscape_keeps_only_google_cookies(tmp_path: Path) -> None:
    dest = tmp_path / "cookies.txt"
    write_netscape_cookies(
        dest,
        [
            {"domain": ".example.com", "path": "/", "secure": True, "expires": 1, "name": "sid", "value": "nope"},
            {"domain": ".google.com", "path": "/", "secure": True, "expires": 1, "name": "SID", "value": "yes"},
        ],
    )
    text = dest.read_text(encoding="utf-8")
    assert "SID" in text
    assert "nope" not in text
    assert "example.com" not in text


def test_import_cookies_txt_drops_unrelated_domains(tmp_path: Path) -> None:
    source = tmp_path / "export.txt"
    source.write_text(
        "\n".join(
            [
                "# Netscape HTTP Cookie File",
                ".bank.example\tTRUE\t/\tTRUE\t0\tsess\tsecret",
                ".google.com\tTRUE\t/\tTRUE\t0\tSID\tabc",
                "",
            ]
        ),
        encoding="utf-8",
    )
    dest = tmp_path / "cookies.txt"
    import_cookies_txt(source, dest)
    text = dest.read_text(encoding="utf-8")
    assert "SID" in text
    assert "secret" not in text
    assert "bank.example" not in text


def test_clear_session_secrets_keeps_db_and_downloads(tmp_path: Path) -> None:
    data = tmp_path / "gpsync-data"
    output = tmp_path / "photos"
    data.mkdir()
    output.mkdir()
    settings = Settings(
        data_dir=data,
        output_dir=output,
        cookies_path=data / "cookies.txt",
        db_path=data / "photos.db",
    )
    settings.cookies_path.write_text("SID\tsecret\n", encoding="utf-8")
    profile = settings.browser_profile_dir
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Cookies").write_text("chromium", encoding="utf-8")
    settings.db_path.write_bytes(b"sqlite")
    (data / "photos.db-wal").write_bytes(b"wal")
    settings.config_path.write_text("{}", encoding="utf-8")
    photo = output / "albums" / "trip" / "IMG_1.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"jpeg")

    removed = clear_session_secrets(settings)
    assert not settings.cookies_path.exists()
    assert not profile.exists()
    assert settings.db_path.read_bytes() == b"sqlite"
    assert (data / "photos.db-wal").read_bytes() == b"wal"
    assert settings.config_path.read_text(encoding="utf-8") == "{}"
    assert photo.read_bytes() == b"jpeg"
    assert {path.name for path in removed} == {"cookies.txt", "browser-profile"}
