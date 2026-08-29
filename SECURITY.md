# Security

## Session data (treat as a password)

`gpsync-data/` is local-only and listed in `.gitignore`. It can contain:

- `cookies.txt` - Google session cookies
- `browser-profile/` - Chromium profile from `gpsync login`
- `photos.db` - album/media inventory (not the photos themselves)
- `config.json` - output path and settings

Do not commit, zip, or share that directory. After `gpsync login` / cookie import the files are created with owner-only permissions where the OS allows it.

`gpsync logout` deletes only `cookies.txt` and `browser-profile/`. It does not delete `photos.db`, `config.json`, or the download output directory.

Cookie import (`gpsync login --cookies`) keeps only Google-related domains; it does not copy the rest of a full-browser export.

## What this project installs

Direct runtime dependencies (pinned in `pyproject.toml`):

| Package | Source | Role |
|---|---|---|
| `playwright==1.62.0` | PyPI ([microsoft/playwright-python](https://github.com/microsoft/playwright-python), Apache-2.0) | Login window |
| `requests==2.34.2` | PyPI ([psf/requests](https://github.com/psf/requests), Apache-2.0) | HTTP |
| `tqdm==4.70.0` | PyPI ([tqdm/tqdm](https://github.com/tqdm/tqdm), MPL-2.0 AND MIT) | Progress bar |

`pip install` will still pull **transitive** packages those three declare (for example `urllib3`, `certifi`, `greenlet`). Those are not listed in this repo; they come from PyPI at install time.

`playwright install chromium` downloads a Chromium build from Microsoft's distribution, not from this repository.

This project does **not** pip-install xob0t's repositories. RPC offsets were reimplemented here (MIT). Their license is in `third_party/xob0t-LICENSE`.

The optional Chrome extension mentioned in the README is not bundled. Prefer `gpsync login` if you do not want third-party browser extensions.

## What we do not do

- No telemetry, no network calls except to Google Photos / Google login / download CDNs you already use in the browser, plus PyPI when you install.
- No GitHub Actions except `actions/checkout` and `actions/setup-python` (CI tests).
- No post-install scripts that fetch extra Python code from random URLs.

## Updating dependencies

Versions are pinned on purpose so `pip install -e .` does not silently move to a new major release. To upgrade, change the pins in `pyproject.toml`, run the tests, and review the upstream changelog.

## Reporting a vulnerability

If you find a bug that leaks cookies or writes outside the output / data directories, open a **private** GitHub security advisory if the repo has Advisories enabled; otherwise do not attach `cookies.txt` to a public issue.
