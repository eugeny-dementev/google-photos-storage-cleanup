from __future__ import annotations

from gpsync.client import PhotosClient
from gpsync.config import Settings
from gpsync.db import LIBRARY_ALBUM_KEY, Database
from gpsync.parser import Album, MediaItem
from gpsync.util import unix_seconds


def enumerate_all(
    settings: Settings,
    *,
    include_library: bool = False,
    album_filter: str | None = None,
    refresh: bool = False,
) -> None:
    settings.ensure_dirs()
    client = PhotosClient(settings.cookies_path, settings.account_index, settings.request_pause_s)
    print(f"Signed in as {client.email}")

    with Database(settings.db_path) as db:
        albums = _collect_albums(client)
        if include_library:
            albums.insert(
                0,
                Album(media_key=LIBRARY_ALBUM_KEY, title="Library", is_shared=False, item_count=None),
            )

        if album_filter:
            needle = album_filter.casefold()
            albums = [album for album in albums if album.title and needle in album.title.casefold()]

        print(f"Found {len(albums)} album(s) to scan")
        for album in albums:
            source = "library" if album.media_key == LIBRARY_ALBUM_KEY else (
                "shared_link" if album.auth_key and album.is_shared else "albums"
            )
            db.upsert_album(
                album.media_key,
                album.title,
                is_shared=album.is_shared,
                auth_key=album.auth_key,
                owner_name=None,
                item_count=album.item_count,
                source=source,
            )
            existing = db.get_album(album.media_key)
            if existing and existing["status"] == "enumerated" and not refresh:
                print(f"  skip (already enumerated): {album.title or album.media_key}")
                continue
            print(f"  scanning: {album.title or album.media_key}")
            try:
                count = _enumerate_album(db, client, album)
            except Exception as exc:  # noqa: BLE001
                print(f"    failed: {exc}")
                continue
            db.mark_album_enumerated(album.media_key, count)
            print(f"    {count} items")


def _collect_albums(client: PhotosClient) -> list[Album]:
    by_key: dict[str, Album] = {}
    for album in client.iter_albums():
        if album.media_key:
            by_key[album.media_key] = album
    for link in client.iter_shared_links():
        if not link.media_key or link.media_key in by_key:
            continue
        by_key[link.media_key] = Album(
            media_key=link.media_key,
            title=None,
            item_count=link.item_count,
            is_shared=True,
            auth_key=link.auth_key,
            owner_actor_id=link.owner.actor_id if link.owner else None,
        )
    return list(by_key.values())


def _enumerate_album(db: Database, client: PhotosClient, album: Album) -> int:
    keys: list[str] = []
    for item in _album_item_iter(db, client, album):
        if not item.media_key:
            continue
        _store_item(db, album.media_key, item)
        keys.append(item.media_key)
        if len(keys) % 200 == 0:
            db._conn.commit()
            print(f"    … {len(keys)} items")
    db._conn.commit()

    for offset in range(0, len(keys), 80):
        chunk = keys[offset : offset + 80]
        try:
            infos = client.get_batch_media_info(chunk)
        except Exception as exc:  # noqa: BLE001 — shared items often reject batch info
            print(f"    batch metadata skipped: {exc}")
            continue
        with db.transaction():
            for info in infos:
                if not info.media_key:
                    continue
                db.upsert_media(
                    info.media_key,
                    file_name=info.file_name,
                    taken_ts=unix_seconds(info.timestamp),
                    timezone_offset=info.timezone_offset,
                    created_ts=unix_seconds(info.creation_timestamp),
                    size_bytes=info.size,
                    is_video=_looks_like_video(info.file_name),
                )
    return len(keys)


def _album_item_iter(db: Database, client: PhotosClient, album: Album):
    if album.media_key == LIBRARY_ALBUM_KEY:
        yield from client.iter_library()
        return

    first = client.get_album_page(album.media_key, None, album.auth_key)
    if first.title or first.auth_key or first.owner:
        db.upsert_album(
            album.media_key,
            first.title or album.title,
            is_shared=album.is_shared or bool(first.auth_key),
            auth_key=first.auth_key or album.auth_key,
            owner_name=first.owner.name if first.owner else None,
            item_count=first.item_count or album.item_count,
        )
    yield from first.items
    page_id = first.next_page_id
    auth_key = first.auth_key or album.auth_key
    while page_id:
        page = client.get_album_page(album.media_key, page_id, auth_key)
        yield from page.items
        page_id = page.next_page_id


def _store_item(db: Database, album_key: str, item: MediaItem) -> None:
    db.upsert_media(
        item.media_key,
        dedup_key=item.dedup_key,
        taken_ts=unix_seconds(item.timestamp),
        timezone_offset=item.timezone_offset,
        created_ts=unix_seconds(item.creation_timestamp),
        width=item.width,
        height=item.height,
        is_video=(item.video_duration > 0) if item.video_duration is not None else None,
        is_live_photo=item.is_live_photo,
        is_owned=item.is_owned,
    )
    db.add_album_item(album_key, item.media_key)


def _looks_like_video(file_name: str | None) -> bool | None:
    if not file_name:
        return None
    return file_name.lower().rsplit(".", 1)[-1] in {"mp4", "mov", "m4v", "avi", "mkv", "webm", "3gp"}
