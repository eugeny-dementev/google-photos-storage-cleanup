from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
FILE_DATE_PREFIX = re.compile(r"^\d{4}\.\d{2}\.\d{2} \d{2}\.\d{2} ")
ALBUM_DATE_PREFIX = re.compile(r"^\d{4}\.\d{2}\.\d{2} ")
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT]I?B?|B)?\s*$", re.IGNORECASE)


def safe_get(data: Any, *keys: Any, default: Any = None) -> Any:
    current = data
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif (
            isinstance(current, list)
            and isinstance(key, int)
            and -len(current) <= key < len(current)
        ):
            current = current[key]
        else:
            return default
    return current


def parse_wiz_global_data(page: str) -> dict[str, Any]:
    marker = "window.WIZ_global_data = "
    start = page.find(marker)
    if start < 0:
        raise ValueError("WIZ_global_data not found — session is missing or expired")
    start += len(marker)
    data, _end = json.JSONDecoder().raw_decode(page[start:])
    if not isinstance(data, dict):
        raise ValueError("WIZ_global_data is not a JSON object")
    return data


def unix_seconds(ts: int | float | None) -> int | None:
    """Normalize Google Photos timestamps (s / ms / us) to unix seconds."""
    if ts is None:
        return None
    value = int(ts)
    if value > 10**14:
        return value // 1_000_000
    if value > 10**11:
        return value // 1_000
    if value <= 0:
        return None
    return value


def timestamp_to_datetime(ts: int | float | None) -> datetime | None:
    seconds = unix_seconds(ts)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def capture_datetime(taken_ts: int | float | None, timezone_offset: int | None = None) -> datetime | None:
    """Capture time in the photo's timezone when Google provides an offset."""
    dt = timestamp_to_datetime(taken_ts)
    if dt is None:
        return None
    if timezone_offset is None:
        return dt
    try:
        offset = int(timezone_offset)
    except (TypeError, ValueError):
        return dt
    # Google usually sends seconds; small values are minutes (e.g. 180 = UTC+3).
    delta = timedelta(minutes=offset) if abs(offset) <= 16 * 60 else timedelta(seconds=offset)
    try:
        return dt.astimezone(timezone(delta))
    except (OSError, ValueError):
        return dt


def parse_size(value: str) -> int:
    """Parse 50M / 50MB / 1.5G / 1024 into bytes (1024-based)."""
    match = _SIZE_RE.match(value or "")
    if not match:
        raise ValueError(f"Invalid size {value!r}. Examples: 50M, 100KB, 1.5G")
    number = float(match.group(1))
    unit = (match.group(2) or "B").upper().replace("IB", "B")
    multipliers = {
        "B": 1,
        "K": 1024,
        "KB": 1024,
        "M": 1024**2,
        "MB": 1024**2,
        "G": 1024**3,
        "GB": 1024**3,
        "T": 1024**4,
        "TB": 1024**4,
    }
    if unit not in multipliers:
        raise ValueError(f"Invalid size unit in {value!r}")
    return int(number * multipliers[unit])


def parse_pause(value: str) -> tuple[float, float]:
    """Parse 3 or 1-5 into (min_seconds, max_seconds)."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Pause cannot be empty. Examples: 3, 1-5")
    if "-" in raw:
        left, right = raw.split("-", 1)
        try:
            low = float(left.strip())
            high = float(right.strip())
        except ValueError as exc:
            raise ValueError(f"Invalid pause {value!r}. Examples: 3, 1-5") from exc
    else:
        try:
            low = high = float(raw)
        except ValueError as exc:
            raise ValueError(f"Invalid pause {value!r}. Examples: 3, 1-5") from exc
    if low < 0 or high < 0 or low > high:
        raise ValueError(f"Invalid pause {value!r}. Use 0 or more, and min <= max.")
    return low, high


FOLDER_SPEC_MAX = 10


def parse_folder_spec(value: str) -> list[int]:
    """Parse --folder 1,4-6,10 into sorted unique 1-based indexes. No spaces."""
    if not value:
        raise ValueError("Empty --folder. Examples: 3, 1,4,9, 2-5, 1,4-6,10")
    if any(ch.isspace() for ch in value):
        raise ValueError("Do not use spaces in --folder (example: 1,4-6,10)")
    seen: set[int] = set()
    numbers: list[int] = []
    for part in value.split(","):
        if not part:
            raise ValueError(f"Invalid --folder {value!r} (example: 1,4-6,10)")
        if "-" in part:
            left, sep, right = part.partition("-")
            if not sep or not left.isdigit() or not right.isdigit():
                raise ValueError(f"Invalid --folder item {part!r} (example: 1,4-6,10)")
            start, end = int(left), int(right)
            if start < 1 or end < 1:
                raise ValueError("--folder numbers start at 1")
            if start > end:
                raise ValueError(f"Range {part} is backwards (use {end}-{start})")
            chunk = range(start, end + 1)
        elif part.isdigit():
            start = int(part)
            if start < 1:
                raise ValueError("--folder numbers start at 1")
            chunk = range(start, start + 1)
        else:
            raise ValueError(f"Invalid --folder item {part!r} (example: 1,4-6,10)")
        for index in chunk:
            if index not in seen:
                seen.add(index)
                numbers.append(index)
            if len(numbers) > FOLDER_SPEC_MAX:
                raise ValueError(f"--folder selects at most {FOLDER_SPEC_MAX} albums")
    numbers.sort()
    return numbers


def format_bytes(size: int | float | None) -> str:
    if size is None:
        return "?"
    size = float(size)
    for unit, step in (("T", 1024**4), ("G", 1024**3), ("M", 1024**2), ("K", 1024)):
        if abs(size) >= step:
            value = size / step
            text = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{text}{unit}"
    return f"{int(size)}B"


def size_allowed(size: int | None, min_size: int | None, max_size: int | None) -> bool:
    if size is None:
        return True
    if min_size is not None and size < min_size:
        return False
    if max_size is not None and size > max_size:
        return False
    return True


def with_album_date_prefix(name: str, taken: datetime | None) -> str:
    if taken is None or ALBUM_DATE_PREFIX.match(name):
        return name
    return f"{taken.strftime('%Y.%m.%d')} {name}"


def with_file_date_prefix(name: str, taken: datetime | None) -> str:
    if taken is None or FILE_DATE_PREFIX.match(name):
        return name
    # Colon is illegal in Windows filenames; use HH.MM.
    return f"{taken.strftime('%Y.%m.%d %H.%M')} {name}"


def sanitize_filename(name: str, fallback: str = "unnamed") -> str:
    name = (name or "").strip().replace("\n", " ").replace("\r", " ")
    name = INVALID_FS_CHARS.sub("_", name)
    name = name.rstrip(" .")
    return name or fallback


def unique_path(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    filename = sanitize_filename(filename)
    dest = directory / filename
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    index = 2
    while True:
        candidate = directory / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def filename_from_content_disposition(header: str | None) -> str | None:
    if not header:
        return None
    star = re.search(r"filename\*\s*=\s*([^']*)''([^;]+)", header, re.IGNORECASE)
    if star:
        return sanitize_filename(unquote(star.group(2).strip().strip('"')))
    plain = re.search(r'filename\s*=\s*"([^"]+)"', header, re.IGNORECASE)
    if plain:
        return sanitize_filename(plain.group(1))
    plain = re.search(r"filename\s*=\s*([^;]+)", header, re.IGNORECASE)
    if plain:
        return sanitize_filename(plain.group(1).strip().strip('"'))
    return None


def extension_from_content_type(content_type: str | None) -> str:
    if not content_type:
        return ""
    mime = content_type.split(";")[0].strip().lower()
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/heic": ".heic",
        "image/heif": ".heif",
        "image/avif": ".avif",
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
        "video/x-matroska": ".mkv",
        "video/webm": ".webm",
        "application/zip": ".zip",
    }
    return mapping.get(mime, "")
