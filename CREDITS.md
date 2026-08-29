# Credits

This file is the human-readable attribution for third-party work and for what is original to this repository. The license grant is in [LICENSE](LICENSE).

## Protocol (not a library)

The official Google Photos Library API (since March 2025) cannot list a user's library or shared albums. This project does **not** use that API.

Instead it calls the same undocumented `PhotosUi` `batchexecute` RPCs that [photos.google.com](https://photos.google.com/) uses when you scroll the library and open albums (`/_/PhotosUi/data/batchexecute`). Session cookies from a real Google login are required. Google can change these RPCs without notice.

RPC ids used here include `Z5xsfc` (albums), `F2A0H` (shared links), `snAcKc` (album page), `lcxiM` (library timeline), `VrseUb` (item / original download URL), `fDcn4b` (extended item info), `EWgK9e` (batch media info), `pLFTfd` / `yCLA7` / `dnv2s` (download URL / token).

## Derived from xob0t (MIT)

Request shapes and nested-JSON field offsets for those RPCs are derived from:

- [xob0t/Google-Photos-Toolkit](https://github.com/xob0t/Google-Photos-Toolkit) - TypeScript userscript / toolkit that maps Photos web RPCs and parsers
- [xob0t/google_photos_web_client](https://github.com/xob0t/google_photos_web_client) - Python client for the same `batchexecute` protocol (cookies, `WIZ_global_data`, payload layout)

Copyright (c) 2024 xob0t, MIT License.

**How it was used:** the protocol map (RPC ids, argument lists, parser offsets) was reimplemented in `gpsync/parser.py` and `gpsync/client.py`. Neither repository is vendored, copied as a subtree, or listed as a pip dependency. The CLI, SQLite queue, and downloader are not from those projects.

That derivation is also noted in [LICENSE](LICENSE). The upstream MIT text is copied at [third_party/xob0t-LICENSE](third_party/xob0t-LICENSE).

## Runtime and build dependencies

Declared and **version-pinned** in `pyproject.toml` so a later PyPI release is not picked up until you change the pin. Each package keeps its own license. This repo does not vendor their source.

| Project | Role |
|---|---|
| [Playwright](https://playwright.dev/python/) 1.62.0 | Chromium window for `gpsync login`; cookies saved as Netscape `cookies.txt` |
| [requests](https://requests.readthedocs.io/) 2.34.2 | HTTP to `batchexecute` and original-file downloads |
| [tqdm](https://tqdm.github.io/) 4.70.0 | Download progress bar |
| [Hatchling](https://hatch.pypa.io/) | Package build backend (not a runtime dependency) |
| [pytest](https://pytest.org/) | Tests (`pip install -e ".[dev]"`) |

The standard library is used for SQLite, `argparse`, `pathlib`, and `http.cookiejar.MozillaCookieJar` (Netscape cookie files).

## Tools mentioned in the docs (not dependencies)

[Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) is an optional Chrome extension for the "import cookies" login path. It is not bundled or required if you use `gpsync login` with Playwright.

## Looked at, not used

These were reviewed while choosing an approach and are **not** sources of this codebase:

- Official Photos Library API - insufficient for library + shared albums after the 2025 restriction
- [gabrielgrijincu/let-my-photos-go](https://github.com/gabrielgrijincu/let-my-photos-go) and similar Playwright "scroll the grid" exporters - architecture here is direct RPCs, not DOM scraping
- Google Takeout, rclone, and older `gphotos-sync`-style tools that relied on the official API

## Original to this project

Everything else is written for this repository, including:

- `gpsync` CLI (`init`, `login`, `logout`, `whoami`, `enumerate`, `download`, `status`, `verify`)
- Local SQLite state (`pending` / `downloaded` / `failed` per album+item) so long runs can stop and resume
- Album-folder layout, duplicate-in-two-albums copy-from-disk (no second HTTP download)
- HTTP original download (including Live Photo companion video and zip unwrap)
- File timestamps: mtime from capture date; Windows creation time when on `nt`
- Playwright login plus Netscape cookie import, Linux display / Chromium-deps error hints
- Tests under `tests/`
- Size filters, status table, `--folder` / `-y`, optional capture-time name prefixes
