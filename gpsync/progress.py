from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import IO, TextIO

from tqdm import tqdm

ANSI_RE = re.compile(r"\033\[[0-9;]*m")
LETTER_ORDER = "dsicz!."
LETTER_COLOR = {
    "d": "\033[32m",
    "s": "\033[33m",
    "i": "\033[36m",
    "c": "\033[34m",
    "z": "\033[35m",
    ".": "\033[2m",
    "!": "\033[31m",
}
DONE_COLOR = "\033[2m"
RESET = "\033[0m"
COLLAPSE_WORKERS = 8
_CURSOR_UP = "\033[{}A"
_CLEAR_EOL = "\033[K"


def vislen(text: str) -> int:
    return len(ANSI_RE.sub("", text))


def _paint(text: str, code: str, color: bool) -> str:
    if not color or not code:
        return text
    return f"{code}{text}{RESET}"


def format_worker_cluster(
    letters: list[str],
    *,
    n_workers: int,
    budget: int,
    color: bool,
) -> str:
    letters = [ch for ch in letters if ch]
    if not letters or budget < 1:
        return ""
    packed = "".join(_paint(ch, LETTER_COLOR.get(ch, ""), color) for ch in letters)
    collapse = n_workers > COLLAPSE_WORKERS or vislen(packed) > budget
    if not collapse:
        return packed
    counts = Counter(letters)
    parts: list[str] = []
    for ch in LETTER_ORDER:
        n = counts[ch]
        if n <= 0:
            continue
        glyph = _paint(ch, LETTER_COLOR.get(ch, ""), color)
        parts.append(glyph if n == 1 else f"{n}{glyph}")
    collapsed = "".join(parts)
    if vislen(collapsed) <= budget:
        return collapsed
    return ""


def format_album_line(
    title: str,
    *,
    letters: list[str],
    done: bool,
    width: int,
    color: bool,
    n_workers: int,
) -> str:
    width = max(20, width)

    def left(text: str) -> str:
        return f'Downloading album "{text}"'

    def fit(title_text: str, drop_letters: bool) -> str | None:
        head = left(title_text)
        if done:
            right = _paint("done", DONE_COLOR, color)
        elif drop_letters:
            right = ""
        else:
            gap_budget = width - vislen(head) - 1
            right = format_worker_cluster(
                letters, n_workers=n_workers, budget=max(0, gap_budget), color=color
            )
        need = vislen(head) + (1 if right else 0) + vislen(right)
        if need > width:
            return None
        if not right:
            return head
        gap = max(1, width - vislen(head) - vislen(right))
        return head + (" " * gap) + right

    title_work = title
    for drop_letters in (False, True):
        while True:
            line = fit(title_work, drop_letters=drop_letters)
            if line is not None:
                return line
            if len(title_work) <= 1:
                break
            keep = max(1, len(title_work) - 4)
            title_work = title_work[:keep] + "..."
        title_work = title
    return left(title)[:width]


@dataclass
class _Album:
    key: str
    title: str
    remaining: int
    done: bool = False


@dataclass
class _Worker:
    album_key: str | None = None
    letter: str = "."


@dataclass
class DownloadStatus:
    n_workers: int
    albums: dict[str, _Album] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    workers: dict[int, _Worker] = field(default_factory=dict)

    def plan(self, remaining: dict[str, int], titles: dict[str, str]) -> None:
        for key, count in remaining.items():
            self.albums[key] = _Album(key=key, title=titles.get(key) or key, remaining=count)

    def set_worker(self, slot: int, album_key: str, title: str, letter: str) -> bool:
        album = self.albums.get(album_key)
        if album is None:
            album = _Album(key=album_key, title=title or album_key, remaining=1)
            self.albums[album_key] = album
        elif title:
            album.title = title
        first = album_key not in self.order
        if first:
            self.order.append(album_key)
        self.workers[slot] = _Worker(album_key=album_key, letter=letter)
        return first

    def clear_worker(self, slot: int) -> None:
        worker = self.workers.get(slot)
        if worker is not None:
            worker.album_key = None
            worker.letter = "."

    def complete_item(self, album_key: str) -> bool:
        album = self.albums.get(album_key)
        if album is None:
            return False
        album.remaining = max(0, album.remaining - 1)
        if album.remaining > 0:
            return False
        album.done = True
        if album_key not in self.order:
            self.order.append(album_key)
        return True

    def letters_for(self, album_key: str) -> list[str]:
        letters: list[str] = []
        for slot in sorted(self.workers):
            worker = self.workers[slot]
            if worker.album_key == album_key and worker.letter and worker.letter != ".":
                letters.append(worker.letter)
        return letters

    def lines(self, *, width: int, color: bool) -> list[str]:
        out: list[str] = []
        for key in self.order:
            album = self.albums[key]
            out.append(
                format_album_line(
                    album.title,
                    letters=self.letters_for(key),
                    done=album.done,
                    width=width,
                    color=color,
                    n_workers=self.n_workers,
                )
            )
        return out


class WorkerSlots:
    def __init__(self, n: int) -> None:
        self._local = threading.local()
        self._lock = threading.Lock()
        self._free = list(range(n))

    def current(self) -> int:
        slot = getattr(self._local, "slot", None)
        if slot is not None:
            return slot
        with self._lock:
            slot = self._free.pop() if self._free else 0
        self._local.slot = slot
        return slot


def _enable_windows_vt(stream: IO[str]) -> bool:
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle_id = -12 if stream is sys.stderr else -11
        handle = kernel32.GetStdHandle(handle_id)
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:  # noqa: BLE001
        return False


def _use_color(stream: IO[str]) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


class DownloadDisplay:
    """Header + album lines + tqdm bar. Album lines stay; workers update the right edge."""

    def __init__(
        self,
        *,
        total: int,
        workers: int,
        suffix: str = "",
        stdout: TextIO = sys.stdout,
        stderr: TextIO = sys.stderr,
    ) -> None:
        self.status = DownloadStatus(n_workers=workers)
        self.slots = WorkerSlots(workers)
        self._lock = threading.RLock()
        self._stderr = stderr
        self._tty = bool(getattr(stderr, "isatty", lambda: False)())
        vt = _enable_windows_vt(stderr) if self._tty else False
        self._live = self._tty and vt
        self._color = self._live and _use_color(stderr)
        self._height = 0
        self._last_draw = 0.0
        self._pending = False
        print(
            f"Downloading {total} item(s) with {workers} worker(s){suffix}",
            file=stdout,
            flush=True,
        )
        self._bar = tqdm(
            total=total,
            unit="file",
            file=stderr,
            dynamic_ncols=True,
            mininterval=0.2,
            disable=False,
        )

    def _ncols(self) -> int:
        cols = getattr(self._bar, "ncols", None)
        if not cols:
            cols = shutil.get_terminal_size((80, 24)).columns
        return max(20, int(cols))

    def _erase_live(self) -> None:
        fp = self._bar.fp
        self._bar.clear()
        if self._height:
            fp.write(_CURSOR_UP.format(self._height))
            for _ in range(self._height):
                fp.write(f"\r{_CLEAR_EOL}\n")
            fp.write(_CURSOR_UP.format(self._height))
        self._height = 0
        fp.flush()

    def _redraw(self, *, force: bool = False) -> None:
        if not self._live:
            return
        now = time.monotonic()
        if not force and now - self._last_draw < 0.1:
            self._pending = True
            return
        width = self._ncols()
        lines = self.status.lines(width=width, color=self._color)
        fp = self._bar.fp
        self._bar.clear()
        if self._height:
            fp.write(_CURSOR_UP.format(self._height))
        for line in lines:
            fp.write(f"\r{line}{_CLEAR_EOL}\n")
        leftover = self._height - len(lines)
        for _ in range(max(0, leftover)):
            fp.write(f"\r{_CLEAR_EOL}\n")
        if leftover > 0:
            fp.write(_CURSOR_UP.format(leftover))
        self._height = len(lines)
        self._bar.refresh()
        fp.flush()
        self._last_draw = now
        self._pending = False

    def set_worker(self, slot: int, album_key: str, title: str, letter: str) -> None:
        with self._lock:
            first = self.status.set_worker(slot, album_key, title, letter)
            if self._live:
                self._redraw(force=first or letter == "!")
            elif first:
                print(f'Downloading album "{title}"', file=self._stderr, flush=True)

    def clear_worker(self, slot: int) -> None:
        with self._lock:
            self.status.clear_worker(slot)
            if self._live:
                self._redraw()

    def advance(self, album_key: str) -> None:
        with self._lock:
            just_done = self.status.complete_item(album_key)
            self._bar.update(1)
            if self._live:
                self._redraw(force=just_done or self._pending)

    def log(self, message: str) -> None:
        with self._lock:
            if self._live:
                self._erase_live()
                tqdm.write(message, file=self._bar.fp)
                self._redraw(force=True)
            else:
                tqdm.write(message, file=self._bar.fp)

    def close(self) -> None:
        with self._lock:
            if self._live:
                self._redraw(force=True)
            self._bar.close()
            self._height = 0

    def __enter__(self) -> DownloadDisplay:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
