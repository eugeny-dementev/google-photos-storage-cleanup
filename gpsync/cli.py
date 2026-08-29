from __future__ import annotations

import argparse
from pathlib import Path

from gpsync.auth import clear_session_secrets, import_cookies_txt, login_with_browser
from gpsync.client import PhotosClient, SessionExpired
from gpsync.config import load_settings
from gpsync.db import Database
from gpsync.download import download_pending
from gpsync.enumerate import enumerate_all
from gpsync.util import parse_pause, parse_size


def _arg_size(value: str) -> int:
    try:
        return parse_size(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _arg_pause(value: str) -> tuple[float, float]:
    try:
        return parse_pause(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gpsync",
        description="Download Google Photos albums (including shared) in original quality.",
    )
    parser.add_argument("--data-dir", type=Path, help="State directory (default: ./gpsync-data)")
    parser.add_argument("--output-dir", type=Path, help="Where to save files (default: ./photos)")
    parser.add_argument("--account-index", type=int, help="N in photos.google.com/u/N/ for multi-login cookies")
    parser.add_argument("-y", "--yes", action="store_true", help="Assume yes for confirmation prompts")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create data directory and save output path")
    init.add_argument("--output", type=Path, required=True, help="Download destination")

    login = sub.add_parser("login", help="Log in via Chromium and save cookies")
    login.add_argument("--cookies", type=Path, help="Import an existing Netscape cookies.txt instead")

    logout = sub.add_parser(
        "logout",
        help="Delete cookies and Chromium profile; keep SQLite and downloaded files",
    )
    logout.add_argument("-y", "--yes", action="store_true", default=argparse.SUPPRESS, help="Assume yes for prompts")

    sub.add_parser("whoami", help="Check that cookies still work")

    enum = sub.add_parser("enumerate", help="List albums and items into SQLite")
    enum.add_argument("--library", action="store_true", help="Also scan the main library timeline")
    enum.add_argument("--album", help="Only albums whose title contains this text")
    enum.add_argument("--refresh", action="store_true", help="Re-scan albums already in the database")

    dl = sub.add_parser("download", help="Download pending items")
    dl.add_argument("-y", "--yes", action="store_true", default=argparse.SUPPRESS, help="Assume yes for prompts")
    dl.add_argument("--album", help="Only this album (title substring)")
    dl.add_argument(
        "--folder",
        type=int,
        metavar="N",
        help="Album number from `gpsync status` (1-based row index)",
    )
    dl.add_argument("--limit", type=int, help="Stop after N files (useful for a test run)")
    dl.add_argument("--workers", type=int, help="Parallel HTTP downloads (default: 2)")
    dl.add_argument("--failed-only", action="store_true", help="Retry previously failed items")
    dl.add_argument("--min-size", type=_arg_size, metavar="SIZE", help="Only files at least this large (e.g. 50M)")
    dl.add_argument("--max-size", type=_arg_size, metavar="SIZE", help="Only files at most this large (e.g. 50M)")
    dl.add_argument(
        "--date-prefix",
        action="store_true",
        help="Prefix albums with YYYY.MM.DD and files with YYYY.MM.DD HH.MM (capture time)",
    )
    dl.add_argument(
        "--pause",
        type=_arg_pause,
        metavar="SEC",
        help="Wait between starting each file, e.g. 3 or 1-5 (seconds). Use --workers 1 to avoid overlap.",
    )

    sub.add_parser("status", help="Show SQLite progress")

    verify = sub.add_parser("verify", help="Check downloaded files still exist")
    verify.add_argument("--reset", action="store_true", help="Mark missing files as pending")

    args = parser.parse_args(argv)
    assume_yes = bool(getattr(args, "yes", False))
    settings = load_settings(
        data_dir=args.data_dir,
        output_dir=args.output_dir if args.command != "init" else None,
        account_index=args.account_index,
        workers=getattr(args, "workers", None),
    )

    try:
        if args.command == "init":
            settings.output_dir = args.output
            settings.save()
            print(f"Data:   {settings.data_dir.resolve()}")
            print(f"Output: {settings.output_dir.resolve()}")
            return 0
        if args.command == "login":
            settings.ensure_dirs()
            if args.cookies:
                import_cookies_txt(args.cookies, settings.cookies_path)
                print(f"Imported cookies to {settings.cookies_path}")
            else:
                login_with_browser(settings)
            settings.save()
            return 0
        if args.command == "logout":
            return _logout(settings, assume_yes=assume_yes)
        if args.command == "whoami":
            client = PhotosClient(settings.cookies_path, settings.account_index)
            print(client.email)
            return 0
        if args.command == "enumerate":
            enumerate_all(
                settings,
                include_library=args.library,
                album_filter=args.album,
                refresh=args.refresh,
            )
            _print_status(settings)
            return 0
        if args.command == "download":
            min_size = getattr(args, "min_size", None)
            max_size = getattr(args, "max_size", None)
            if min_size is not None and max_size is not None and min_size > max_size:
                print("--min-size cannot be greater than --max-size")
                return 2
            album_key = _resolve_download_album(
                settings,
                album=args.album,
                folder=args.folder,
                assume_yes=assume_yes,
            )
            download_pending(
                settings,
                album_key=album_key,
                album_filter=args.album if args.folder is None else None,
                limit=args.limit,
                workers=args.workers,
                failed_only=args.failed_only,
                min_size=min_size,
                max_size=max_size,
                date_prefix=args.date_prefix,
                pause=args.pause,
            )
            _print_status(settings)
            return 0
        if args.command == "status":
            _print_status(settings)
            return 0
        if args.command == "verify":
            return _verify(settings, reset=args.reset)
    except SessionExpired as exc:
        print(exc)
        return 2
    return 1


def _resolve_download_album(
    settings,
    *,
    album: str | None,
    folder: int | None,
    assume_yes: bool,
) -> str | None:
    """Return album media_key, or None to download every album."""
    if album and folder is not None:
        raise SystemExit("Use either --folder N or --album NAME, not both.")
    if folder is None:
        return None
    if not settings.db_path.exists():
        raise SystemExit("No database yet. Run: gpsync enumerate")
    with Database(settings.db_path) as db:
        rows = db.list_album_status()
        if folder < 1 or folder > len(rows):
            raise SystemExit(f"No folder #{folder}. `gpsync status` lists 1..{len(rows) or 0}.")
        row = rows[folder - 1]
        title = row["title"] or row["media_key"]
        total = int(row["total"] or 0)
        downloaded = int(row["downloaded"] or 0)
        prompt = (
            f'You\'re about to download folder #{folder} "{title}" '
            f"({downloaded}/{total} already downloaded). Continue?"
        )
        if not _confirm(prompt, assume_yes=assume_yes):
            raise SystemExit("Cancelled.")
        return row["media_key"]


def _logout(settings, *, assume_yes: bool) -> int:
    prompt = (
        "Delete session secrets (cookies.txt and Chromium profile)? "
        "SQLite and downloaded files will be kept."
    )
    if not _confirm(prompt, assume_yes=assume_yes):
        raise SystemExit("Cancelled.")
    removed = clear_session_secrets(settings)
    if not removed:
        print("No session files found.")
    else:
        for path in removed:
            print(f"Removed {path}")
    print(f"Kept database: {settings.db_path}")
    print(f"Kept downloads: {settings.output_dir}")
    print("Sign in again with: gpsync login")
    return 0


def _confirm(prompt: str, *, assume_yes: bool) -> bool:
    if assume_yes:
        print(f"{prompt} [y/N]")
        print("Assuming yes (-y).")
        return True
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _print_status(settings) -> None:
    if not settings.db_path.exists():
        print("No database yet. Run: gpsync enumerate")
        return
    with Database(settings.db_path) as db:
        counts = db.status()
        print(
            f"albums={counts.albums}  media={counts.media}  "
            f"pending={counts.pending}  downloaded={counts.downloaded}  "
            f"failed={counts.failed}  skipped={counts.skipped}"
        )
        rows = db.list_album_status()
        if not rows:
            return
        print()
        print(format_status_table(rows))
        print()
        print("Use: gpsync download --folder N")


def format_status_table(rows) -> str:
    """Render numbered album rows for `gpsync status` and `--folder`."""
    records: list[tuple[str, str, str, str, str, str]] = []
    for index, row in enumerate(rows, start=1):
        title = row["title"] or row["media_key"]
        total = int(row["total"] or 0)
        downloaded = int(row["downloaded"] or 0)
        failed = int(row["failed"] or 0)
        scanned = (row["enumerated_at"] or "")[:16] or "-"
        records.append(
            (
                str(index),
                f'"{title}"',
                "yes" if row["is_shared"] else "no",
                f"{downloaded}/{total}",
                str(failed),
                scanned,
            )
        )

    headers = ("#", "album", "shared", "done/total", "fail", "scanned")
    widths = [len(h) for h in headers]
    for rec in records:
        for i, cell in enumerate(rec):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: tuple[str, ...] | tuple[str, str, str, str, str, str]) -> str:
        parts = [
            cells[0].rjust(widths[0]),
            cells[1].ljust(widths[1]),
            cells[2].rjust(widths[2]),
            cells[3].rjust(widths[3]),
            cells[4].rjust(widths[4]),
            cells[5].ljust(widths[5]),
        ]
        return "  ".join(parts)

    lines = [fmt_row(headers), fmt_row(tuple("-" * w for w in widths))]
    lines.extend(fmt_row(rec) for rec in records)
    return "\n".join(lines)


def _verify(settings, *, reset: bool) -> int:
    missing = 0
    with Database(settings.db_path) as db:
        for row in db.downloaded_rows():
            path = row["local_path"]
            if not path or not Path(path).exists():
                missing += 1
                print(f"missing: {path or row['media_key']}")
                if reset:
                    db.mark_item(row["id"], "pending", error="missing on disk")
    print(f"{missing} missing file(s)")
    return 0 if missing == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
