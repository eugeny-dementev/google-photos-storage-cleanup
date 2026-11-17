# Independent Google Photos storage cleanup

This installation extends trialuser/google-photos-sync commit 44c31feecc57ea6bc18b019f643a0ecdc0e5c294.
It has its own virtual environment, cookies, browser profile, SQLite state, and reports.
It does not read local photo backups or interact with Ente or previous cleanup scripts.
The existing cookie file was imported once. A fresh independent login is currently required.
The original installation and database remain unchanged.

## Commands

Run PowerShell in this directory. Replace EMAIL with the exact signed-in Google account.

```powershell
.\storage.ps1 scan --expect-account EMAIL
.\storage.ps1 scan --expect-account EMAIL --resume SCAN_ID
.\storage.ps1 report --scan-id SCAN_ID
```

Scanning reads the current Library and Archive, both ordinary and accessible shared albums, and per-item details.
It never downloads image files or changes Google Photos content.
Metadata requests use two workers with a shared request limiter.
Completed item details are committed individually. Resume refreshes library presence and all album memberships.
Cached metadata younger than one hour can be reused during resume; application always refreshes eligibility.
Incomplete scans cannot authorize Trash.
A completed scan produces items.json, items.csv, summary.json and quota-before.json under gpsync-data/reports/SCAN_ID.
State is stored separately in gpsync-data/storage.sqlite.
JSON contains identifiers, ownership, source, quality code, capture/creation timestamps, dimensions, storage evidence, albums, and exclusion reasons.
The CSV escapes spreadsheet formula prefixes. Display names alone never establish ownership.
Charged byte totals use Google-reported amounts, not downloaded file sizes.
Quality codes and dates never authorize deletion. Free, uncertain, conflicting, or album-associated items stay protected.
Archived photos, videos and Live Photos use the same rules.

## Validate metadata before enabling Trash

Compare representative photos against Google Photos Info panels. Record evidence in a JSON array:

```json
[
  {
    "media_key": "COPY_FROM_REPORT",
    "is_owned": true,
    "takes_space": true,
    "albums": [],
    "quality_code": 1,
    "ui_checked_at": "ACTUAL_INSPECTION_TIME",
    "notes": "Describe what the Google Photos Info panel showed."
  }
]
```

Do not use this example as evidence. Values must match actual inspections and report values.
The gate requires externally owned, free owned, eligible, album-associated, and charged compressed examples.
Also inspect available original-quality, pre-2021, Pixel-exempt, partner-shared, archived, video and Live Photo examples.
Missing sample classes keep Trash disabled. Raw quality code 2 means Original; other codes are preserved without guessing compression subtype.

```powershell
.\storage.ps1 validate --scan-id SCAN_ID --evidence .\ui-evidence.json
```

Validation binds the evidence to the exact report digest. Rescanning invalidates validation.
Review the CSV and JSON before using the report SHA-256 for application.

```powershell
.\storage.ps1 trash --scan-id SCAN_ID --expect-account EMAIL --reviewed-report REPORT_SHA256 --limit 10
```

The default limit is ten confirmed mutations per invocation. Increase it explicitly for a reviewed larger run.
Each action refreshes per-item details and cross-checks independent storage evidence.
Current library ownership and the complete album index are refreshed at application startup.
All known aliases sharing a mutation identifier must be approved and remain eligible.
The tool sends one item per request, without automatic POST retries.
It confirms results through Trash and journals intent, confirmation, skipped items, and uncertain results.
Reruns reconcile uncertain results against Trash. Unresolved results stop the run and require investigation.
It never empties Trash or switches to permanent deletion.
Google controls Trash retention; quota updates may be delayed.
Storage results remain pending server accounting and never cause extra deletions.
Cloud deletion can propagate to devices with Google Photos backup enabled.
This tool does not verify or require a local recoverable backup.

## Authentication and network

The installation has a private copy of the configured HTTP proxy in gpsync-data/network.json.
The PowerShell launcher sets proxy variables for its child process and restores them afterward.
The independent browser login uses the same private network configuration.
To refresh login, run `.venv\Scripts\python.exe -m gpsync login` interactively.
After the library appears, press Enter in that terminal to export cookies and close the browser.
Never copy or share gpsync-data: it contains session credentials and private metadata.
Login expiration stops work. No existing browser profile is opened or modified.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Tests cover original downloader behavior, conservative selection, pagination failures, storage conflicts,
account binding, UI validation gates, timeout journaling, and reconciliation without repeat mutation.

## Cleanup browser proxy extension

The cleanup browser loads a private copy of Proxy Switcher and Manager from the main Chrome profile.
Its separate extension settings preserve the copied profiles and add VLESS SOCKS.
VLESS SOCKS uses SOCKS5 through the existing Nginx listener at 127.0.0.1:1180.
Login selects that profile before navigating to Google Photos.
The scanner uses VS Code's http.proxy setting, independently of the browser extension.
The PowerShell launcher refreshes that setting before each run.
The HTTP client pins HTTP and HTTPS to that proxy and ignores environment bypass rules.
Browser login preserves the scanner proxy setting.
The main Chrome profile, Nginx configuration, and VLESS services are not modified.
Google Accounts returned HTTP 200 through this route during verification.

## Incremental cleanup

The incremental mode does not wait for the entire library audit.
It first completes the ordinary/shared album membership index, including protected mutation identifiers.
It then examines library pages containing up to 100 items, including Archive.
Each eligible item requires confirmed ownership, positive charged bytes, two agreeing Google storage flags,
and no album association by media identifier or mutation identifier.
Both metadata responses are refreshed immediately before each Trash action.
Free, externally owned, uncertain, conflicting, or album-associated items are preserved.
No local backup check is required.

```powershell
.\storage.ps1 stream --expect-account EMAIL --write --limit 10
.\storage.ps1 stream --expect-account EMAIL --write --limit 0 --resume JOB_ID
```

Without --write, streaming only audits metadata. Zero means no per-run mutation limit.
The current user explicitly authorized incremental writes after live metadata capability checks.
This mode therefore uses independent live API agreement instead of the complete-scan UI-evidence gate.
The original scan/report/trash workflow keeps its separate UI-evidence and report-review requirements.
The initial ten-item pilot must be confirmed before starting an unlimited continuation.
Confirmed actions are journaled and are not repeated on resume.
An uncertain mutation is reconciled against Trash. Unresolved outcomes stop the process without retrying deletion.
A resume begins library enumeration again; already confirmed actions remain protected by the journal.
Pagination during mutation may skip items. A later pass can handle remaining eligible items.
Per-item metadata and confirmations appear in scan.log. Errors appear in scan-errors.log.
Reports and pending quota snapshots are written when the run stops or reaches its limit.

## macOS installation and isolated authorization

```sh
git clone https://github.com/eugeny-dementev/google-photos-storage-cleanup.git
cd google-photos-storage-cleanup
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m playwright install chromium
.venv/bin/python -m gpsync login
```

Use Python 3.11 or newer. Homebrew Python is usually /opt/homebrew/bin/python3 on Apple Silicon.
Create a private gpsync-data/network.json containing your chosen HTTP proxy before using storage commands.
Do not copy another account's cookies or browser profile.

For a browser launched through SSH:

```sh
.venv/bin/python -u -m gpsync login --wait-file gpsync-data/login-ready
```

After the account's library is visible, create that confirmation file from the controlling SSH session.
The browser then exports cookies only into this installation and closes.
Verify the account with whoami before any scan or streaming write.

```sh
.venv/bin/python -m gpsync whoami
.venv/bin/python -u -m gpsync storage stream --expect-account YOUR_EMAIL --write --limit 10
```

The scanner reads network.json directly and pins its HTTP client to that proxy.
Run the ten-item pilot first. Check confirmations before continuing with --limit 0 --resume JOB_ID.
Use `tail -n 30 -f scan.log` to observe logs on macOS.
Use `tail -n 30 -f scan-errors.log` in another terminal for errors.
Git ignores session cookies, browser profiles, inventory databases, reports, and logs.

For macOS installations where bundled Chromium cannot access the configured proxy, use the installed Chrome app with an isolated profile:

```sh
GPSYNC_BROWSER_CHANNEL=chrome .venv/bin/python -u -m gpsync login --wait-file gpsync-data/login-ready
```

The private profile still lives in this installation; it never opens the normal Chrome profile.

network.json may set browser_proxy separately from proxy.
Use a socks5 or socks5h URL for browser_proxy when login requires the VLESS SOCKS route.
The scanner continues to use proxy; browser login cannot replace that setting.
