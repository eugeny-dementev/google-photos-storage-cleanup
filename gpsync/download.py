from __future__ import annotations

import random
import shutil
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from tqdm import tqdm

from gpsync.client import PhotosApiError, PhotosClient, SessionExpired
from gpsync.config import Settings
from gpsync.db import LIBRARY_ALBUM_KEY, Database
from gpsync.progress import DownloadDisplay
from gpsync.timestamps import set_file_times
from gpsync.util import (
    ALBUM_DATE_PREFIX,
    capture_datetime,
    extension_from_content_type,
    filename_from_content_disposition,
    format_bytes,
    sanitize_filename,
    size_allowed,
    unique_path,
    with_file_date_prefix,
)

_thread = threading.local()


class SizeFilterSkip(Exception):
    """Item does not match --min-size / --max-size for this run."""


class Interrupted(Exception):
    """Ctrl+C: stop starting new files; keep SQLite and finished downloads."""


@dataclass(frozen=True)
class DownloadOptions:
    min_size: int | None = None
    max_size: int | None = None
    date_prefix: bool = False
    pause_min: float | None = None
    pause_max: float | None = None


_start_lock = threading.Lock()
_next_start = 0.0
_stop = threading.Event()


def request_stop() -> None:
    _stop.set()


def stop_requested() -> bool:
    return _stop.is_set()


def _wait_download_slot(options: DownloadOptions) -> None:
    if _stop.is_set():
        raise Interrupted()
    if options.pause_min is None or options.pause_max is None:
        return
    delay = random.uniform(options.pause_min, options.pause_max)
    global _next_start
    with _start_lock:
        now = time.monotonic()
        start_at = max(now, _next_start)
        _next_start = start_at + delay
        wait = start_at - now
    deadline = time.monotonic() + wait
    while wait > 0:
        if _stop.is_set():
            raise Interrupted()
        left = deadline - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(left, 0.2))


def download_pending(
    settings: Settings,
    *,
    album_key: str | None = None,
    album_keys: list[str] | None = None,
    album_filter: str | None = None,
    limit: int | None = None,
    workers: int | None = None,
    failed_only: bool = False,
    min_size: int | None = None,
    max_size: int | None = None,
    date_prefix: bool = False,
    pause: tuple[float, float] | None = None,
) -> None:
    settings.ensure_dirs()
    workers = max(1, workers or settings.workers)
    global _next_start
    _stop.clear()
    with _start_lock:
        _next_start = 0.0
    options = DownloadOptions(
        min_size=min_size,
        max_size=max_size,
        date_prefix=date_prefix,
        pause_min=None if pause is None else pause[0],
        pause_max=None if pause is None else pause[1],
    )
    with Database(settings.db_path) as db:
        selected_keys = list(album_keys) if album_keys else None
        if selected_keys is None and album_key is None and album_filter:
            matches = db.find_albums(album_filter)
            if not matches:
                raise SystemExit(f"No album matching {album_filter!r}")
            if len(matches) > 1:
                names = ", ".join(row["title"] or row["media_key"] for row in matches[:8])
                raise SystemExit(f"Ambiguous album filter {album_filter!r}: {names}")
            selected_keys = [matches[0]["media_key"]]
        elif selected_keys is None and album_key:
            selected_keys = [album_key]
        items = db.pending_items(
            album_keys=selected_keys,
            failed_only=failed_only,
            min_size=min_size,
            max_size=max_size,
        )
        if limit is not None:
            items = items[:limit]
        total = len(items)
        if total == 0:
            print("Nothing to download.")
            return
        extra = []
        if min_size is not None:
            extra.append(f"min {format_bytes(min_size)}")
        if max_size is not None:
            extra.append(f"max {format_bytes(max_size)}")
        if pause is not None:
            low, high = pause
            extra.append(f"pause {low:g}-{high:g}s" if low != high else f"pause {low:g}s")
        suffix = f" ({', '.join(extra)})" if extra else ""
        remaining: dict[str, int] = {}
        titles: dict[str, str] = {}
        for item in items:
            key = item["album_key"]
            remaining[key] = remaining.get(key, 0) + 1
            titles[key] = item["album_title"] or key

        album_dates: dict[str, str | None] = {}
        if date_prefix:
            keys = {item["album_key"] for item in items}
            for key in keys:
                row = db.album_earliest_taken(key)
                if row is None:
                    album_dates[key] = None
                    continue
                taken = capture_datetime(row["taken_ts"], row["timezone_offset"])
                album_dates[key] = taken.strftime("%Y.%m.%d") if taken else None

    rows = [dict(item) for item in items]
    for row in rows:
        row["_album_date"] = album_dates.get(row["album_key"])
    errors = 0
    skipped = 0
    display = DownloadDisplay(total=total, workers=workers, suffix=suffix)
    display.status.plan(remaining, titles)
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {
            pool.submit(_download_one, settings, row, options, display): row for row in rows
        }
        for future in as_completed(futures):
            row = futures[future]
            try:
                future.result()
            except SizeFilterSkip as exc:
                skipped += 1
                display.log(f"skip: {exc}")
            except (Interrupted, CancelledError):
                continue
            except SessionExpired:
                raise
            except Exception as exc:  # noqa: BLE001
                errors += 1
                display.log(f"error: {exc}")
            display.advance(row["album_key"])
    except KeyboardInterrupt:
        request_stop()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        display.close()
    if skipped:
        print(f"Skipped {skipped} item(s) due to size filter (still pending).")
    if errors:
        print(f"Finished with {errors} error(s). Re-run: gpsync download --failed-only")


def fix_downloaded_times(
    settings: Settings,
    *,
    album_keys: list[str] | None = None,
    album_filter: str | None = None,
) -> int:
    """Set mtime/ctime on already downloaded files from SQLite capture dates. No HTTP."""
    if not settings.db_path.exists():
        print("No database yet. Run: gpsync enumerate")
        return 2
    with Database(settings.db_path) as db:
        selected = list(album_keys) if album_keys else None
        if selected is None and album_filter:
            matches = db.find_albums(album_filter)
            if not matches:
                raise SystemExit(f"No album matching {album_filter!r}")
            if len(matches) > 1:
                names = ", ".join(row["title"] or row["media_key"] for row in matches[:8])
                raise SystemExit(f"Ambiguous album filter {album_filter!r}: {names}")
            selected = [matches[0]["media_key"]]
        rows = [dict(row) for row in db.downloaded_rows(album_keys=selected)]

    if not rows:
        print("No downloaded files to update.")
        return 0

    print(f"Updating file times for {len(rows)} downloaded item(s) (paths from SQLite, no download)")
    updated = 0
    missing = 0
    no_date = 0
    for row in tqdm(rows, unit="file"):
        taken = capture_datetime(row["taken_ts"], row["timezone_offset"])
        if taken is None:
            no_date += 1
            continue
        paths = [row["local_path"], row["companion_path"]]
        saw_file = False
        for raw in paths:
            if not raw:
                continue
            path = Path(raw)
            if not path.exists():
                missing += 1
                continue
            set_file_times(path, taken)
            updated += 1
            saw_file = True
        if not saw_file and not row["local_path"]:
            missing += 1

    print(f"Updated {updated} file(s). Missing on disk: {missing}. No capture date in DB: {no_date}.")
    return 0 if missing == 0 else 1


def _thread_resources(settings: Settings) -> tuple[PhotosClient, Database]:
    client = getattr(_thread, "client", None)
    db = getattr(_thread, "db", None)
    if client is None:
        client = PhotosClient(settings.cookies_path, settings.account_index, settings.request_pause_s)
        _thread.client = client
    if db is None:
        db = Database(settings.db_path)
        _thread.db = db
    return client, db


def _album_label(row: dict) -> str:
    return row.get("album_title") or row.get("album_key") or "album"


def _download_one(
    settings: Settings,
    row: dict,
    options: DownloadOptions,
    display: DownloadDisplay | None = None,
) -> None:
    slot = display.slots.current() if display is not None else None
    album_key = row["album_key"]
    title = _album_label(row)
    failed = False

    def state(letter: str) -> None:
        if display is not None and slot is not None:
            display.set_worker(slot, album_key, title, letter)

    try:
        if _stop.is_set():
            raise Interrupted()
        if options.pause_min is not None:
            state("s")
        _wait_download_slot(options)
        client, db = _thread_resources(settings)
        item_id = row["id"]
        taken = capture_datetime(row["taken_ts"], row["timezone_offset"])
        album_dir = _album_dir(
            settings.output_dir,
            row["album_title"],
            row["album_key"],
            date_prefix=row.get("_album_date") if options.date_prefix else None,
        )
        state("i")
        size = row.get("size_bytes")
        if size is None and (options.min_size is not None or options.max_size is not None):
            size = _probe_size(client, db, row)
        if not size_allowed(size, options.min_size, options.max_size):
            raise SizeFilterSkip(
                f"{row.get('file_name') or row['media_key']} "
                f"({format_bytes(size)}, want {_size_filter_label(options)})"
            )

        existing = _existing_copy(db, row["media_key"], item_id)
        if existing:
            existing_size = existing.stat().st_size
            db.update_media_size(row["media_key"], existing_size)
            if not size_allowed(existing_size, options.min_size, options.max_size):
                raise SizeFilterSkip(
                    f"{existing.name} ({format_bytes(existing_size)}, want {_size_filter_label(options)})"
                )
            dest_name = existing.name
            if options.date_prefix:
                dest_name = with_file_date_prefix(dest_name, taken)
            dest = unique_path(album_dir, dest_name)
            state("c")
            shutil.copy2(existing, dest)
            set_file_times(dest, taken)
            db.mark_item(item_id, "downloaded", local_path=str(dest), bytes_written=dest.stat().st_size)
            return

        rpc_album = None if row["album_key"] == LIBRARY_ALBUM_KEY else row["album_key"]
        info = client.get_item_info(row["media_key"], rpc_album, row["auth_key"])
        if info is None:
            raise PhotosApiError("Empty item info from Photos")
        url = info.download_original_url or info.download_url
        if not url:
            url = client.get_download_url([row["media_key"]], row["auth_key"])
        if not url:
            raise PhotosApiError("No original download URL")

        preferred = row["file_name"] or _probe_filename(client, row)
        dest = _http_download(
            client,
            url,
            album_dir,
            preferred,
            row["media_key"],
            taken,
            date_prefix=options.date_prefix,
            min_size=options.min_size,
            max_size=options.max_size,
            on_state=state,
        )
        if dest.stat().st_size and size is None:
            db.update_media_size(row["media_key"], dest.stat().st_size)
        companion = None
        if info.live_photo_video_download_url:
            try:
                companion = _http_download(
                    client,
                    info.live_photo_video_download_url,
                    album_dir,
                    f"{dest.stem}.mov",
                    row["media_key"],
                    taken,
                    date_prefix=False,
                    min_size=options.min_size,
                    max_size=options.max_size,
                    on_state=state,
                )
            except SizeFilterSkip:
                companion = None
        db.mark_item(
            item_id,
            "downloaded",
            local_path=str(dest),
            companion_path=str(companion) if companion else None,
            bytes_written=dest.stat().st_size,
        )
    except Interrupted:
        raise
    except SizeFilterSkip:
        raise
    except SessionExpired:
        failed = True
        state("!")
        db.mark_item(item_id, "failed", error="session expired")
        raise
    except Exception as exc:  # noqa: BLE001
        failed = True
        state("!")
        db.mark_item(item_id, "failed", error=str(exc)[:500])
        raise
    finally:
        if display is not None and slot is not None and not failed:
            display.clear_worker(slot)


def _probe_filename(client: PhotosClient, row: dict) -> str | None:
    try:
        ext = client.get_item_info_ext(row["media_key"], row["auth_key"])
        return ext.file_name
    except Exception:  # noqa: BLE001
        return None


def _probe_size(client: PhotosClient, db: Database, row: dict) -> int | None:
    try:
        ext = client.get_item_info_ext(row["media_key"], row["auth_key"])
    except Exception:  # noqa: BLE001
        return row.get("size_bytes")
    if ext.size:
        db.update_media_size(row["media_key"], ext.size)
        return ext.size
    return row.get("size_bytes")


def _size_filter_label(options: DownloadOptions) -> str:
    parts = []
    if options.min_size is not None:
        parts.append(f">={format_bytes(options.min_size)}")
    if options.max_size is not None:
        parts.append(f"<={format_bytes(options.max_size)}")
    return ", ".join(parts) or "any"


def _existing_copy(db: Database, media_key: str, current_id: int) -> Path | None:
    row = db._conn.execute(
        """
        SELECT local_path FROM album_items
        WHERE media_key = ? AND status = 'downloaded' AND id != ? AND local_path IS NOT NULL
        LIMIT 1
        """,
        (media_key, current_id),
    ).fetchone()
    if not row or not row["local_path"]:
        return None
    path = Path(row["local_path"])
    return path if path.exists() else None


def _album_dir(
    output_dir: Path,
    title: str | None,
    album_key: str,
    date_prefix: str | None = None,
) -> Path:
    if album_key == LIBRARY_ALBUM_KEY:
        name = "library"
    else:
        name = sanitize_filename(title or album_key[:16], fallback=album_key[:16])
    if date_prefix and not ALBUM_DATE_PREFIX.match(name):
        name = f"{date_prefix} {name}"
    if album_key == LIBRARY_ALBUM_KEY:
        return output_dir / name
    return output_dir / "albums" / name


def _http_download(
    client: PhotosClient,
    url: str,
    dest_dir: Path,
    preferred_name: str | None,
    media_key: str,
    taken,
    *,
    date_prefix: bool = False,
    min_size: int | None = None,
    max_size: int | None = None,
    on_state: Callable[[str], None] | None = None,
) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    if _stop.is_set():
        raise Interrupted()
    if on_state is not None:
        on_state("d")
    with client.session.get(url, stream=True, timeout=600, allow_redirects=True) as response:
        if "accounts.google.com" in response.url:
            raise SessionExpired("Download redirected to Google login")
        content_type = response.headers.get("content-type", "")
        if response.status_code >= 400:
            raise PhotosApiError(f"HTTP {response.status_code} downloading {media_key}")
        if "text/html" in content_type and response.status_code == 200:
            raise SessionExpired("Download returned HTML (session expired or blocked)")

        length = response.headers.get("content-length")
        declared = int(length) if length and length.isdigit() else None
        if declared is not None and not size_allowed(declared, min_size, max_size):
            raise SizeFilterSkip(f"{media_key} ({format_bytes(declared)} from Content-Length)")

        filename = (
            sanitize_filename(preferred_name) if preferred_name else None
        ) or filename_from_content_disposition(response.headers.get("content-disposition"))
        if not filename:
            filename = media_key[:24] + extension_from_content_type(content_type)
        if date_prefix:
            filename = with_file_date_prefix(filename, taken)
        dest = unique_path(dest_dir, filename)
        tmp = dest.with_name(dest.name + ".partial")
        written = 0
        try:
            with tmp.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if _stop.is_set():
                        raise Interrupted()
                    if not chunk:
                        continue
                    written += len(chunk)
                    if max_size is not None and written > max_size:
                        raise SizeFilterSkip(
                            f"{filename} grew past max {format_bytes(max_size)}"
                        )
                    handle.write(chunk)
        except (SizeFilterSkip, Interrupted):
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(dest)

    if min_size is not None and dest.stat().st_size < min_size:
        size = dest.stat().st_size
        dest.unlink(missing_ok=True)
        raise SizeFilterSkip(f"{filename} ({format_bytes(size)} < min {format_bytes(min_size)})")

    if dest.suffix.lower() == ".zip" or zipfile.is_zipfile(dest):
        if on_state is not None:
            on_state("z")
        extracted = _extract_zip(dest, dest_dir, taken, date_prefix=date_prefix)
        dest.unlink(missing_ok=True)
        if not extracted:
            raise PhotosApiError("Empty zip from Google Photos")
        return extracted[0]

    set_file_times(dest, taken)
    return dest


def _extract_zip(zip_path: Path, dest_dir: Path, taken, *, date_prefix: bool = False) -> list[Path]:
    written: list[Path] = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if date_prefix:
                name = with_file_date_prefix(name, taken)
            dest = unique_path(dest_dir, name)
            with archive.open(info) as src, tempfile.NamedTemporaryFile(delete=False, dir=dest_dir) as tmp:
                shutil.copyfileobj(src, tmp)
                tmp_path = Path(tmp.name)
            tmp_path.replace(dest)
            set_file_times(dest, taken)
            written.append(dest)
    still = [path for path in written if path.suffix.lower() not in {".mov", ".mp4"}]
    return still + [path for path in written if path not in still]
