from pathlib import Path

from gpsync.auth import _is_google_domain, import_cookies_txt, write_netscape_cookies


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
