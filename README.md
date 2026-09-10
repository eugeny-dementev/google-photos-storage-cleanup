# Google Photos Sync

**English:** this file | **Русский:** [docs/README.ru.md](docs/README.ru.md)

Download **Google Photos albums** (including albums shared with you) at original quality. Python, local SQLite, browser-cookie login. Runs on **Linux**, Windows, and macOS.

This is an **unofficial** client, not affiliated with Google. It calls the same internal `batchexecute` RPCs that [photos.google.com](https://photos.google.com/) uses when you scroll the library and open albums (`/_/PhotosUi/data/batchexecute`).

The official Photos Library API has not been able to read your library or shared albums since March 2025.

Use at your own risk. Google's [Terms of Service](https://policies.google.com/terms) forbid using automated means to access content in violation of machine-readable instructions such as [photos.google.com/robots.txt](https://photos.google.com/robots.txt) (which disallows `/albums`, `/photo/`, `/shared`, and similar paths). Google may suspend or terminate an account for a material or repeated breach; see [why accounts are disabled](https://support.google.com/accounts/answer/40695).

## Features

- Every album, including shared ones. The same photo in two albums is saved **twice** (the second copy is taken from disk, not downloaded again).
- Photos and videos, including items you do not own.
- Original files: the web UI "Download original" URL (Shift+D), not an API transcode.
- Original filename (Google metadata or `Content-Disposition`).
- File date = capture time from Google Photos (mtime; on Windows, creation time as well).
- SQLite statuses `pending` / `downloaded` / `failed` - stop and resume large libraries.

## Install

Python 3.11+.

**Linux / macOS**

```bash
cd google-photos-sync
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/playwright install chromium
```

On Linux, Chromium often needs extra system libraries. If the window fails to open:

```bash
playwright install-deps chromium
```

`install-deps` usually needs `sudo` (Debian/Ubuntu and similar).

**Windows (PowerShell)**

```powershell
cd google-photos-sync
python -m venv .venv
.\.venv\Scripts\pip install -e .
.\.venv\Scripts\playwright install chromium
```

After that you do **not** need to activate the venv. From the project folder:

```bash
./gpsync.sh status       # Linux, macOS, WSL
.\gpsync status          # Windows (cmd or PowerShell)
./run.py status          # same thing, Python launcher
```

The launcher uses `.venv` next to itself, then runs as if you were in the project folder (`gpsync-data` stays there).

## Quick start

```bash
./gpsync.sh init --output ~/Photos/GooglePhotos
./gpsync.sh login
./gpsync.sh enumerate
./gpsync.sh download --limit 5          # small test batch
./gpsync.sh status
./gpsync.sh download                    # everything still pending
```

On Windows the output path can be `D:\Photos\GooglePhotos`.

On disk:

```
~/Photos/GooglePhotos/
  albums/
    Vacation 2024/
      IMG_1234.HEIC
      clip.mp4
    Shared trip/
      photo_from_friend.jpg
```

State lives in `./gpsync-data/` (`cookies.txt`, `photos.db`, `config.json`, Chromium profile). That directory is gitignored - it holds your Google session.

## Sign in

### Option 1 - Chromium window (preferred)

Needs a graphical session. On SSH without X11/Wayland or a headless server, use option 2.

```bash
gpsync login
```

Chromium opens. Sign in to Google, wait until you see your Photos library, return to the terminal, press Enter. Cookies are saved to `gpsync-data/cookies.txt`.

Google sessions expire. If you get redirected to login:

```bash
gpsync login
gpsync download --failed-only
```

### Option 2 - cookies.txt from your browser

Prefer option 1. A browser extension is third-party code this repo does not ship or audit.

1. [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc).
2. Allow it in Incognito (`chrome://extensions` -> extension details).
3. Open a **new Incognito window**, sign in to Google, open [photos.google.com](https://photos.google.com/).
4. Open a blank tab, **close the Photos tab**.
5. Export All Cookies -> save `cookies.txt`.
6. Close Incognito so that session is not also live in the browser (Google invalidates it faster otherwise).

```bash
gpsync login --cookies ~/Downloads/cookies.txt
gpsync whoami
```

On Windows the file is often `C:\Users\you\Downloads\cookies.txt`. Import keeps only Google-domain cookies.

Several Google accounts in one cookie jar (`photos.google.com/u/1/`):

```bash
gpsync --account-index 1 whoami
```

Treat `cookies.txt` as a password. Do not commit or share it. To drop the session without touching the database or downloaded files:

```bash
gpsync logout
```

## Commands

In the table, `gpsync` means `./gpsync.sh` on Linux/macOS/WSL or `.\gpsync` on Windows. No `activate`.

| Command | Purpose |
|---|---|
| `gpsync init --output PATH` | Where to store files |
| `gpsync login` | Sign in via Chromium |
| `gpsync login --cookies FILE` | Import Netscape cookies |
| `gpsync logout` | Delete cookies and Chromium profile; keep SQLite and downloads |
| `gpsync logout -y` | Same, no prompt |
| `gpsync whoami` | Check that the session is alive |
| `gpsync enumerate` | Walk albums into SQLite |
| `gpsync enumerate --library` | Also scan the main Library timeline (and archived) |
| `gpsync enumerate --album "Vacation"` | Only albums whose title contains this text |
| `gpsync enumerate --refresh` | Re-scan albums already in the database (new photos are picked up; download status is kept) |
| `gpsync download` | Download `pending` / `failed` |
| `gpsync download --workers 3` | Parallel HTTP downloads (do not go too high) |
| `gpsync download --failed-only` | Retry errors only |
| `gpsync download --limit 20` | Cap this run |
| `gpsync download --max-size 50M` | Files at most 50MiB |
| `gpsync download --min-size 50M` | Files at least 50MiB |
| `gpsync download --folder 10` | Album number from `gpsync status` (asks Y/N) |
| `gpsync download --folder 1,4-6,10` | Several albums: commas, `N-M` ranges, no spaces, max 10 |
| `gpsync download -y --folder 10` | Same, no prompt |
| `gpsync download --ignore-folder 1,2,4` | Skip those albums this run only (numbers from `gpsync status`) |
| `gpsync ignore --folder 1,2,4` | Permanently skip those albums on `gpsync download` |
| `gpsync ignore -y --folder 1,2,4` | Same, no prompt |
| `gpsync unignore --folder 1,2` | Clear the ignore flag on those albums |
| `gpsync unignore --all` | Clear the ignore flag on every album |
| `gpsync download --date-prefix` | Prefix albums `YYYY.MM.DD ...` and files `YYYY.MM.DD HH.MM ...` (capture time) |
| `gpsync download --pause 3` | Wait 3 seconds between starting each file |
| `gpsync download --pause 1-5` | Random wait 1-5 seconds between file starts |
| `gpsync download --fix-time` | Set capture dates on already downloaded files (no HTTP) |
| `gpsync download --fix-time --folder 1,4` | Same, only those albums |
| `gpsync status` | Album table: `#`, name, shared, done/total, fail, ignore, last enumerate |
| `gpsync verify --reset` | Missing files on disk become `pending` again |

Defaults: `--data-dir gpsync-data`, `--output-dir photos`.

`gpsync status` prints a table. Column `#` is the index for `--folder` / `--ignore-folder` / `gpsync ignore`. Use `1,4,9` or `2-5` or `1,4-6,10` (no spaces). At most 10 albums after ranges are expanded. Album names are quoted. `done/total` is downloaded / rows in the database. `ignore` is `yes` if `gpsync ignore` marked the album. `scanned` is the last enumerate time.

`gpsync download` without `--folder` / `--album` skips permanently ignored albums. `--folder` or `--album` still downloads them. `--ignore-folder` skips extra albums for that run only and does not write SQLite. Combine with `--folder`: `gpsync download --folder 1-10 --ignore-folder 3,5`.

`--date-prefix` is off unless you pass the flag. File times use `HH.MM`, not `HH:MM` (`:` is illegal in Windows names). The album date is the earliest capture in that album.

`--min-size` / `--max-size` accept `50M`, `100KB`, `1.5G` (1024-based). Files that do not match stay `pending` so you can download the other bucket later.

`--pause` spaces out when each file starts. With `--workers` greater than 1, transfers can still overlap; use `--workers 1 --pause 1-5` for a gap after each file finishes before the next starts.

`--fix-time` does not download. It walks `downloaded` rows in SQLite and sets each file's mtime (and Windows creation time) to the capture date from enumerate. It uses the stored `local_path` (and Live Photo companion path), so names from `--date-prefix` are found. `--folder` / `--album` / `--ignore-folder` limit which albums; `--pause`, `--workers`, size filters, and `--date-prefix` are ignored. Permanently ignored albums are skipped unless you pass `--folder` or `--album`.

**Ctrl+C** stops `gpsync download` and `gpsync enumerate`. Files already written and SQLite are kept; in-progress transfers are dropped and stay `pending`. Run the same command again to resume.

During `gpsync download`, each album line may show worker letters on the right:

| Letter | Meaning |
|---|---|
| `d` | HTTP download |
| `s` | waiting (`--pause`) |
| `i` | fetching item metadata |
| `c` | copy from disk (same file already in another album) |
| `z` | unpacking a zip from Google |
| `!` | error |

When the album is finished the letters are replaced with `done`.

## How it works

1. **enumerate** calls the same RPCs as the UI: album list (`Z5xsfc`), shared links (`F2A0H`), album pages (`snAcKc`, pagination like scrolling), optional library (`lcxiM`).
2. SQLite holds `albums`, `media`, `album_items` (status per album+item).
3. **download** uses `downloadOriginalUrl` (`VrseUb`) over HTTP with your cookies. Live Photos also get a `.mov`. Zips from Google are unpacked. File dates are set after the write.

This protocol is undocumented. Google can change RPCs without notice. Default pauses and a low worker count are intentional.

## Limits

- Locked Folder (PIN) is not exported.
- Partner sharing with no album of its own is only included if the photo is already in Library: `gpsync enumerate --library`.
- RPC names and payload shapes can break after a Photos UI update.
- Very large videos take longer; a dropped transfer is marked `failed` - retry with `--failed-only`.

## Security and dependencies

Cookies and the Chromium profile stay in `gpsync-data/` (gitignored). See [SECURITY.md](SECURITY.md).

Runtime dependencies are three PyPI packages with **pinned versions** in `pyproject.toml`. xob0t's code is **not** installed via pip; only the MIT-licensed RPC field map was reimplemented here.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Attribution: [CREDITS.md](CREDITS.md). License: [LICENSE](LICENSE). Upstream MIT text: [third_party/xob0t-LICENSE](third_party/xob0t-LICENSE).
