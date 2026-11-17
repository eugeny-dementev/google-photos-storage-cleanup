from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from gpsync.auth import clear_session_secrets, import_cookies_txt, login_with_browser
from gpsync.client import PhotosClient, SessionExpired
from gpsync.config import load_settings
from gpsync.db import LIBRARY_ALBUM_KEY, Database, number_album_rows
from gpsync.download import download_pending, fix_downloaded_times
from gpsync.enumerate import enumerate_all
from gpsync.progress import _enable_windows_vt
from gpsync.util import parse_folder_spec, parse_pause, parse_size

_BRIGHT_WHITE = "\033[1;97m"
_RESET = "\033[0m"


def _stdout_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    if os.environ.get("FORCE_COLOR"):
        if tty:
            _enable_windows_vt(sys.stdout)
        return True
    if not tty:
        return False
    return _enable_windows_vt(sys.stdout)


def _cmd(command: str) -> str:
    wrapped = f"`{command}`"
    if _stdout_color():
        return f"{_BRIGHT_WHITE}{wrapped}{_RESET}"
    return wrapped


def _print_execute_hints(hints: list[tuple[str, str]]) -> None:
    print("Execute")
    for command, purpose in hints:
        print(f"  {_cmd(command)} - {purpose}")


def _arg_size(value: str) -> int:
    try:
        return parse_size(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _arg_folder(value: str) -> list[int]:
    try:
        return parse_folder_spec(value)
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

    login.add_argument("--wait-file", type=Path, help="Export cookies after this confirmation file appears; useful over SSH")

    logout = sub.add_parser(
        "logout",
        help="Delete cookies and Chromium profile; keep SQLite and downloaded files",
    )
    logout.add_argument("-y", "--yes", action="store_true", default=argparse.SUPPRESS, help="Assume yes for prompts")

    sub.add_parser("whoami", help="Check that cookies still work")

    enum = sub.add_parser("enumerate", help="List albums and items into SQLite")
    enum.add_argument(
        "--library",
        action="store_true",
        help="Scan the main Library timeline (status #0). Download later with `gpsync download --library`",
    )
    enum.add_argument("--album", help="Only albums whose title contains this text")
    enum.add_argument("--refresh", action="store_true", help="Re-scan albums already in the database")

    dl = sub.add_parser("download", help="Download pending items")
    dl.add_argument("-y", "--yes", action="store_true", default=argparse.SUPPRESS, help="Assume yes for prompts")
    dl.add_argument("--album", help="Only this album (title substring)")
    dl.add_argument(
        "--folder",
        type=_arg_folder,
        metavar="SPEC",
        help="Album numbers from `gpsync status`: 0 (library), 3, 1,4,9, 2-5, or 1,4-6,10 (no spaces, max 10)",
    )
    dl.add_argument(
        "--library",
        action="store_true",
        help="Download the main Library timeline (#0) into a `library/` folder. Default download skips it.",
    )
    dl.add_argument(
        "--ignore-folder",
        type=_arg_folder,
        metavar="SPEC",
        help="Skip these album numbers this run only (same SPEC as --folder)",
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
    dl.add_argument(
        "--fix-time",
        action="store_true",
        help="Only set capture dates on already downloaded files (no HTTP). Honors --folder/--album/--library/--ignore-folder.",
    )

    ign = sub.add_parser("ignore", help="Permanently skip albums on `gpsync download`")
    ign.add_argument("-y", "--yes", action="store_true", default=argparse.SUPPRESS, help="Assume yes for prompts")
    ign.add_argument(
        "--folder",
        type=_arg_folder,
        required=True,
        metavar="SPEC",
        help="Album numbers from `gpsync status` to skip from now on",
    )

    unign = sub.add_parser("unignore", help="Clear the ignore flag so albums download again")
    unign.add_argument("-y", "--yes", action="store_true", default=argparse.SUPPRESS, help="Assume yes for prompts")
    unign.add_argument(
        "--folder",
        type=_arg_folder,
        metavar="SPEC",
        help="Album numbers from `gpsync status` to include again",
    )
    unign.add_argument("--all", action="store_true", help="Clear ignore on every album")

    sub.add_parser("status", help="Show SQLite progress")

    verify = sub.add_parser("verify", help="Check downloaded files still exist")
    verify.add_argument("--reset", action="store_true", help="Mark missing files as pending")

    from gpsync.storage import add_commands
    add_commands(sub)

    args = parser.parse_args(argv)
    assume_yes = bool(getattr(args, "yes", False))
    settings = load_settings(
        data_dir=args.data_dir,
        output_dir=args.output_dir if args.command != "init" else None,
        account_index=args.account_index,
        workers=getattr(args, "workers", None),
    )

    try:
        if args.command == "storage":
            from gpsync.storage import run
            return run(args, settings)
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
                if args.wait_file:
                    login_with_browser(settings, ready_file=args.wait_file)
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
            picked = _resolve_download_albums(
                settings,
                album=args.album,
                folder=args.folder,
                ignore_folder=args.ignore_folder,
                include_library=bool(args.library),
                assume_yes=assume_yes,
                intent="fix-time" if args.fix_time else "download",
            )
            if args.fix_time:
                return fix_downloaded_times(
                    settings,
                    album_keys=picked.keys,
                    album_filter=args.album if picked.keys is None else None,
                    exclude_album_keys=picked.exclude_keys,
                )
            min_size = getattr(args, "min_size", None)
            max_size = getattr(args, "max_size", None)
            if min_size is not None and max_size is not None and min_size > max_size:
                print("--min-size cannot be greater than --max-size")
                return 2
            download_pending(
                settings,
                album_keys=picked.keys,
                album_filter=args.album if picked.keys is None else None,
                exclude_album_keys=picked.exclude_keys,
                limit=args.limit,
                workers=args.workers,
                failed_only=args.failed_only,
                min_size=min_size,
                max_size=max_size,
                date_prefix=args.date_prefix,
                pause=args.pause,
            )
            _print_execute_hints([("gpsync status", "to see the status")])
            return 0
        if args.command == "ignore":
            return _set_ignored(settings, folder=args.folder, ignored=True, assume_yes=assume_yes)
        if args.command == "unignore":
            return _unignore(settings, folder=args.folder, clear_all=args.all, assume_yes=assume_yes)
        if args.command == "status":
            _print_status(settings)
            return 0
        if args.command == "verify":
            return _verify(settings, reset=args.reset)
    except SessionExpired as exc:
        print(exc)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted (Ctrl+C). SQLite and downloaded files were kept.")
        if args.command == "download":
            resume = "gpsync download --fix-time" if getattr(args, "fix_time", False) else "gpsync download"
            _print_execute_hints(
                [
                    (resume, "to resume"),
                    ("gpsync status", "to see the status"),
                ]
            )
        elif args.command == "enumerate":
            _print_execute_hints([("gpsync enumerate", "to resume")])
        return 130
    return 1


@dataclass(frozen=True)
class AlbumPick:
    """keys=None means every album; exclude_keys are skipped this run only."""

    keys: list[str] | None
    exclude_keys: list[str]


def _resolve_download_albums(
    settings,
    *,
    album: str | None,
    folder: list[int] | None,
    ignore_folder: list[int] | None = None,
    include_library: bool = False,
    assume_yes: bool,
    intent: str = "download",
) -> AlbumPick:
    """Return album media_keys, or keys=None to include every album except library."""
    if album and (folder is not None or ignore_folder is not None):
        raise SystemExit("Use either --folder/--ignore-folder or --album NAME, not both.")
    if folder is None and ignore_folder is None and not include_library and not album:
        return AlbumPick(keys=None, exclude_keys=[LIBRARY_ALBUM_KEY])
    if not settings.db_path.exists():
        hint = "gpsync enumerate --library" if include_library else "gpsync enumerate"
        raise SystemExit(f"No database yet. Run: {hint}")
    action = "update file times in" if intent == "fix-time" else "download"
    with Database(settings.db_path) as db:
        ignore_keys: list[str] = []
        if ignore_folder is not None:
            ignore_keys = [row["media_key"] for _index, row in _indexed_albums(db, ignore_folder)]

        if folder is None and not include_library and not album:
            exclude_keys = [LIBRARY_ALBUM_KEY, *ignore_keys]
            return AlbumPick(keys=None, exclude_keys=exclude_keys)

        keys: list[str] = []
        selected_folders: list[tuple[int, object]] = []
        if include_library:
            if db.get_album(LIBRARY_ALBUM_KEY) is None:
                raise SystemExit("Library is not in the database. Run: gpsync enumerate --library")
            keys.append(LIBRARY_ALBUM_KEY)
        if folder is not None:
            selected_folders = _indexed_albums(db, folder)
            if ignore_keys:
                skip = set(ignore_keys)
                selected_folders = [
                    (index, row) for index, row in selected_folders if row["media_key"] not in skip
                ]
            if not selected_folders and not keys:
                raise SystemExit("Nothing left to download after --ignore-folder.")
            if selected_folders:
                prompt = _folder_confirm_prompt(selected_folders, action)
                if not _confirm(prompt, assume_yes=assume_yes):
                    raise SystemExit("Cancelled.")
                for _index, row in selected_folders:
                    if row["media_key"] not in keys:
                        keys.append(row["media_key"])
        if album:
            matches = [
                row for row in db.find_albums(album) if row["media_key"] != LIBRARY_ALBUM_KEY
            ]
            if not matches:
                raise SystemExit(
                    f"No album matching {album!r}. The Library timeline is `gpsync download --library`."
                )
            if len(matches) > 1:
                names = ", ".join(row["title"] or row["media_key"] for row in matches[:8])
                raise SystemExit(f"Ambiguous album filter {album!r}: {names}")
            if matches[0]["media_key"] not in keys:
                keys.append(matches[0]["media_key"])

        skip = set(ignore_keys)
        keys = [key for key in keys if key not in skip]
        if not keys:
            raise SystemExit("Nothing left to download after --ignore-folder.")
        return AlbumPick(keys=keys, exclude_keys=[])


def _indexed_albums(db: Database, indexes: list[int]) -> list[tuple[int, object]]:
    numbered = db.numbered_album_status()
    by_index = {index: row for index, row in numbered}
    missing = [index for index in indexes if index not in by_index]
    if missing:
        has_library = 0 in by_index
        album_max = max((i for i in by_index if i > 0), default=0)
        if missing[0] == 0 and not has_library:
            raise SystemExit("No folder #0. Scan the timeline with: gpsync enumerate --library")
        listed = "0, 1.." + str(album_max) if has_library and album_max else (
            f"1..{album_max}" if album_max else "nothing yet"
        )
        raise SystemExit(f"No folder #{missing[0]}. `gpsync status` lists {listed}.")
    return [(index, by_index[index]) for index in indexes]


def _folder_confirm_prompt(selected: list[tuple[int, object]], action: str) -> str:
    if len(selected) == 1:
        index, row = selected[0]
        title = row["title"] or row["media_key"]
        total = int(row["total"] or 0)
        downloaded = int(row["downloaded"] or 0)
        return (
            f'You\'re about to {action} folder #{index} "{title}" '
            f"({downloaded}/{total} already downloaded). Continue?"
        )
    lines = [f"You're about to {action} {len(selected)} folders:"]
    for index, row in selected:
        title = row["title"] or row["media_key"]
        total = int(row["total"] or 0)
        downloaded = int(row["downloaded"] or 0)
        lines.append(f'  #{index} "{title}" ({downloaded}/{total} already downloaded)')
    lines.append("Continue?")
    return "\n".join(lines)


def _set_ignored(settings, *, folder: list[int], ignored: bool, assume_yes: bool) -> int:
    if not settings.db_path.exists():
        raise SystemExit("No database yet. Run: gpsync enumerate")
    action = "permanently skip" if ignored else "stop skipping"
    with Database(settings.db_path) as db:
        selected = _indexed_albums(db, folder)
        prompt = _folder_confirm_prompt(selected, action)
        if not _confirm(prompt, assume_yes=assume_yes):
            raise SystemExit("Cancelled.")
        keys = [row["media_key"] for _index, row in selected]
        n = db.set_albums_ignored(keys, ignored)
    if ignored:
        print(f"Ignored {n} album(s). `gpsync download` will skip them.")
        print("Download one anyway: gpsync download --folder N")
        print("Undo: gpsync unignore --folder N")
    else:
        print(f"Cleared ignore on {n} album(s).")
    return 0


def _unignore(settings, *, folder: list[int] | None, clear_all: bool, assume_yes: bool) -> int:
    if bool(folder) == bool(clear_all):
        raise SystemExit("Use either `gpsync unignore --folder SPEC` or `gpsync unignore --all`.")
    if not settings.db_path.exists():
        raise SystemExit("No database yet. Run: gpsync enumerate")
    if clear_all:
        prompt = "Clear the ignore flag on every album?"
        if not _confirm(prompt, assume_yes=assume_yes):
            raise SystemExit("Cancelled.")
        with Database(settings.db_path) as db:
            n = db.clear_all_ignored()
        print(f"Cleared ignore on {n} album(s).")
        return 0
    assert folder is not None
    return _set_ignored(settings, folder=folder, ignored=False, assume_yes=assume_yes)


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
        print("Use: gpsync download --folder 1,4-6")
        print("     gpsync download --library")
        print("     gpsync ignore --folder 2,5")


def format_status_table(rows) -> str:
    """Render numbered album rows for `gpsync status` and `--folder`."""
    records: list[tuple[str, str, str, str, str, str, str]] = []
    for index, row in number_album_rows(rows):
        title = row["title"] or row["media_key"]
        total = int(row["total"] or 0)
        downloaded = int(row["downloaded"] or 0)
        failed = int(row["failed"] or 0)
        try:
            ignored = int(row["ignored"] or 0)
        except (KeyError, IndexError):
            ignored = 0
        scanned = (row["enumerated_at"] or "")[:16] or "-"
        records.append(
            (
                str(index),
                f'"{title}"',
                "yes" if row["is_shared"] else "no",
                f"{downloaded}/{total}",
                str(failed),
                "yes" if ignored else "no",
                scanned,
            )
        )

    headers = ("#", "album", "shared", "done/total", "fail", "ignore", "scanned")
    widths = [len(h) for h in headers]
    for rec in records:
        for i, cell in enumerate(rec):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: tuple[str, ...]) -> str:
        parts = [
            cells[0].rjust(widths[0]),
            cells[1].ljust(widths[1]),
            cells[2].rjust(widths[2]),
            cells[3].rjust(widths[3]),
            cells[4].rjust(widths[4]),
            cells[5].rjust(widths[5]),
            cells[6].ljust(widths[6]),
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
