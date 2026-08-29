from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS albums (
    media_key TEXT PRIMARY KEY,
    title TEXT,
    is_shared INTEGER NOT NULL DEFAULT 0,
    auth_key TEXT,
    owner_name TEXT,
    item_count INTEGER,
    source TEXT NOT NULL DEFAULT 'albums',
    enumerated_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS media (
    media_key TEXT PRIMARY KEY,
    dedup_key TEXT,
    file_name TEXT,
    taken_ts INTEGER,
    timezone_offset INTEGER,
    created_ts INTEGER,
    width INTEGER,
    height INTEGER,
    size_bytes INTEGER,
    is_video INTEGER,
    is_live_photo INTEGER,
    is_owned INTEGER,
    owner_name TEXT
);

CREATE TABLE IF NOT EXISTS album_items (
    id INTEGER PRIMARY KEY,
    album_key TEXT NOT NULL,
    media_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    local_path TEXT,
    companion_path TEXT,
    error TEXT,
    downloaded_at TEXT,
    bytes_written INTEGER,
    UNIQUE(album_key, media_key),
    FOREIGN KEY(album_key) REFERENCES albums(media_key),
    FOREIGN KEY(media_key) REFERENCES media(media_key)
);

CREATE INDEX IF NOT EXISTS idx_album_items_status ON album_items(status);
CREATE INDEX IF NOT EXISTS idx_album_items_album ON album_items(album_key);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

LIBRARY_ALBUM_KEY = "__library__"


@dataclass
class StatusCounts:
    albums: int
    media: int
    pending: int
    downloaded: int
    failed: int
    skipped: int


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def upsert_album(
        self,
        media_key: str,
        title: str | None,
        is_shared: bool = False,
        auth_key: str | None = None,
        owner_name: str | None = None,
        item_count: int | None = None,
        source: str = "albums",
        status: str = "pending",
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO albums (media_key, title, is_shared, auth_key, owner_name, item_count, source, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(media_key) DO UPDATE SET
                title = COALESCE(excluded.title, albums.title),
                is_shared = excluded.is_shared,
                auth_key = COALESCE(excluded.auth_key, albums.auth_key),
                owner_name = COALESCE(excluded.owner_name, albums.owner_name),
                item_count = COALESCE(excluded.item_count, albums.item_count),
                source = excluded.source
            """,
            (media_key, title, int(is_shared), auth_key, owner_name, item_count, source, status),
        )
        self._conn.commit()

    def mark_album_enumerated(self, media_key: str, item_count: int | None = None) -> None:
        self._conn.execute(
            """
            UPDATE albums
            SET status = 'enumerated', enumerated_at = datetime('now'), item_count = COALESCE(?, item_count)
            WHERE media_key = ?
            """,
            (item_count, media_key),
        )
        self._conn.commit()

    def upsert_media(
        self,
        media_key: str,
        *,
        dedup_key: str | None = None,
        file_name: str | None = None,
        taken_ts: int | None = None,
        timezone_offset: int | None = None,
        created_ts: int | None = None,
        width: int | None = None,
        height: int | None = None,
        size_bytes: int | None = None,
        is_video: bool | None = None,
        is_live_photo: bool | None = None,
        is_owned: bool | None = None,
        owner_name: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO media (
                media_key, dedup_key, file_name, taken_ts, timezone_offset, created_ts,
                width, height, size_bytes, is_video, is_live_photo, is_owned, owner_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(media_key) DO UPDATE SET
                dedup_key = COALESCE(excluded.dedup_key, media.dedup_key),
                file_name = COALESCE(excluded.file_name, media.file_name),
                taken_ts = COALESCE(excluded.taken_ts, media.taken_ts),
                timezone_offset = COALESCE(excluded.timezone_offset, media.timezone_offset),
                created_ts = COALESCE(excluded.created_ts, media.created_ts),
                width = COALESCE(excluded.width, media.width),
                height = COALESCE(excluded.height, media.height),
                size_bytes = COALESCE(excluded.size_bytes, media.size_bytes),
                is_video = CASE WHEN excluded.is_video IS NOT NULL THEN excluded.is_video ELSE media.is_video END,
                is_live_photo = CASE WHEN excluded.is_live_photo IS NOT NULL THEN excluded.is_live_photo ELSE media.is_live_photo END,
                is_owned = COALESCE(excluded.is_owned, media.is_owned),
                owner_name = COALESCE(excluded.owner_name, media.owner_name)
            """,
            (
                media_key,
                dedup_key,
                file_name,
                taken_ts,
                timezone_offset,
                created_ts,
                width,
                height,
                size_bytes,
                int(is_video) if is_video is not None else None,
                int(is_live_photo) if is_live_photo is not None else None,
                int(is_owned) if is_owned is not None else None,
                owner_name,
            ),
        )

    def add_album_item(self, album_key: str, media_key: str) -> None:
        self._conn.execute(
            """
            INSERT INTO album_items (album_key, media_key, status)
            VALUES (?, ?, 'pending')
            ON CONFLICT(album_key, media_key) DO NOTHING
            """,
            (album_key, media_key),
        )

    def list_albums(self) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                """
                SELECT * FROM albums
                ORDER BY CASE media_key WHEN '__library__' THEN 0 ELSE 1 END,
                         title COLLATE NOCASE,
                         media_key
                """
            )
        )

    def list_album_status(self) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                """
                SELECT
                    albums.media_key,
                    albums.title,
                    albums.is_shared,
                    albums.enumerated_at,
                    albums.status,
                    albums.item_count,
                    COUNT(album_items.id) AS total,
                    COALESCE(SUM(CASE WHEN album_items.status = 'downloaded' THEN 1 ELSE 0 END), 0) AS downloaded,
                    COALESCE(SUM(CASE WHEN album_items.status = 'failed' THEN 1 ELSE 0 END), 0) AS failed,
                    COALESCE(SUM(CASE WHEN album_items.status = 'pending' THEN 1 ELSE 0 END), 0) AS pending
                FROM albums
                LEFT JOIN album_items ON album_items.album_key = albums.media_key
                GROUP BY albums.media_key
                ORDER BY CASE albums.media_key WHEN '__library__' THEN 0 ELSE 1 END,
                         albums.title COLLATE NOCASE,
                         albums.media_key
                """
            )
        )

    def album_at_index(self, index: int) -> sqlite3.Row | None:
        rows = self.list_album_status()
        if index < 1 or index > len(rows):
            return None
        return rows[index - 1]

    def get_album(self, media_key: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM albums WHERE media_key = ?", (media_key,)).fetchone()

    def find_albums(self, title_substr: str | None = None) -> list[sqlite3.Row]:
        if not title_substr:
            return self.list_albums()
        return list(
            self._conn.execute(
                """
                SELECT * FROM albums
                WHERE title LIKE ?
                ORDER BY CASE media_key WHEN '__library__' THEN 0 ELSE 1 END,
                         title COLLATE NOCASE,
                         media_key
                """,
                (f"%{title_substr}%",),
            )
        )

    def pending_items(
        self,
        album_key: str | None = None,
        album_keys: list[str] | None = None,
        failed_only: bool = False,
        min_size: int | None = None,
        max_size: int | None = None,
    ) -> list[sqlite3.Row]:
        statuses = ("failed",) if failed_only else ("pending", "failed")
        placeholders = ",".join("?" * len(statuses))
        sql = f"""
            SELECT album_items.id, album_items.album_key, album_items.media_key, album_items.status,
                   albums.title AS album_title, albums.auth_key, albums.is_shared,
                   media.file_name, media.taken_ts, media.timezone_offset, media.is_live_photo,
                   media.is_video, media.is_owned, media.size_bytes
            FROM album_items
            JOIN albums ON albums.media_key = album_items.album_key
            JOIN media ON media.media_key = album_items.media_key
            WHERE album_items.status IN ({placeholders})
        """
        params: list = list(statuses)
        keys = [album_key] if album_key else list(album_keys or [])
        if keys:
            placeholders_keys = ",".join("?" * len(keys))
            sql += f" AND album_items.album_key IN ({placeholders_keys})"
            params.extend(keys)
        if min_size is not None:
            sql += " AND (media.size_bytes IS NULL OR media.size_bytes >= ?)"
            params.append(min_size)
        if max_size is not None:
            sql += " AND (media.size_bytes IS NULL OR media.size_bytes <= ?)"
            params.append(max_size)
        sql += " ORDER BY album_items.id"
        return list(self._conn.execute(sql, params))

    def update_media_size(self, media_key: str, size_bytes: int) -> None:
        self._conn.execute(
            "UPDATE media SET size_bytes = COALESCE(size_bytes, ?) WHERE media_key = ?",
            (size_bytes, media_key),
        )
        self._conn.commit()

    def album_earliest_taken(self, album_key: str) -> sqlite3.Row | None:
        return self._conn.execute(
            """
            SELECT media.taken_ts, media.timezone_offset
            FROM album_items
            JOIN media ON media.media_key = album_items.media_key
            WHERE album_items.album_key = ? AND media.taken_ts IS NOT NULL
            ORDER BY media.taken_ts ASC
            LIMIT 1
            """,
            (album_key,),
        ).fetchone()

    def mark_item(
        self,
        item_id: int,
        status: str,
        *,
        local_path: str | None = None,
        companion_path: str | None = None,
        error: str | None = None,
        bytes_written: int | None = None,
    ) -> None:
        downloaded_at = "datetime('now')" if status == "downloaded" else "downloaded_at"
        self._conn.execute(
            f"""
            UPDATE album_items
            SET status = ?, local_path = COALESCE(?, local_path),
                companion_path = COALESCE(?, companion_path),
                error = ?, bytes_written = COALESCE(?, bytes_written),
                downloaded_at = {downloaded_at}
            WHERE id = ?
            """,
            (status, local_path, companion_path, error, bytes_written, item_id),
        )
        self._conn.commit()

    def status(self) -> StatusCounts:
        albums = self._conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
        media = self._conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]

        def count(status: str) -> int:
            return self._conn.execute(
                "SELECT COUNT(*) FROM album_items WHERE status = ?", (status,)
            ).fetchone()[0]

        return StatusCounts(
            albums=albums,
            media=media,
            pending=count("pending"),
            downloaded=count("downloaded"),
            failed=count("failed"),
            skipped=count("skipped"),
        )

    def downloaded_rows(self, album_keys: list[str] | None = None) -> list[sqlite3.Row]:
        sql = """
            SELECT album_items.*, media.file_name, media.taken_ts, media.timezone_offset
            FROM album_items
            JOIN media ON media.media_key = album_items.media_key
            WHERE album_items.status = 'downloaded'
        """
        params: list = []
        if album_keys:
            placeholders = ",".join("?" * len(album_keys))
            sql += f" AND album_items.album_key IN ({placeholders})"
            params.extend(album_keys)
        return list(self._conn.execute(sql, params))
