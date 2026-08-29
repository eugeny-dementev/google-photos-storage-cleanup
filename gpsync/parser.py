"""Parsers for Google Photos batchexecute RPC payloads.

Field offsets follow the undocumented Photos web UI protocol, as documented by
xob0t/Google-Photos-Toolkit and xob0t/google_photos_web_client (MIT).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gpsync.util import safe_get


@dataclass
class Actor:
    actor_id: str | None = None
    gaia_id: str | None = None
    name: str | None = None

    @classmethod
    def from_data(cls, data) -> Actor:
        return cls(
            actor_id=safe_get(data, 0),
            gaia_id=safe_get(data, 1),
            name=safe_get(data, 11, 0),
        )


@dataclass
class Album:
    media_key: str
    title: str | None = None
    item_count: int | None = None
    is_shared: bool = False
    auth_key: str | None = None
    owner_actor_id: str | None = None
    thumbnail_url: str | None = None

    @classmethod
    def from_data(cls, item_data) -> Album:
        return cls(
            media_key=safe_get(item_data, 0),
            owner_actor_id=safe_get(item_data, 6, 0),
            title=safe_get(item_data, -1, "72930366", 1),
            thumbnail_url=safe_get(item_data, 1, 0),
            item_count=safe_get(item_data, -1, "72930366", 3),
            is_shared=bool(safe_get(item_data, -1, "72930366", 4) or False),
            auth_key=safe_get(item_data, -1, "72930366", 5),
        )


@dataclass
class AlbumsPage:
    items: list[Album] = field(default_factory=list)
    next_page_id: str | None = None

    @classmethod
    def from_data(cls, data) -> AlbumsPage:
        return cls(
            items=[Album.from_data(item) for item in safe_get(data, 0, default=[]) or []],
            next_page_id=safe_get(data, 1),
        )


@dataclass
class SharedLink:
    media_key: str | None = None
    auth_key: str | None = None
    link_id: str | None = None
    item_count: int | None = None
    owner: Actor | None = None

    @classmethod
    def from_data(cls, data) -> SharedLink:
        return cls(
            item_count=safe_get(data, 3),
            media_key=safe_get(data, 6),
            auth_key=safe_get(data, 7),
            link_id=safe_get(data, 17),
            owner=Actor.from_data(safe_get(data, 10, 0)),
        )


@dataclass
class SharedLinksPage:
    items: list[SharedLink] = field(default_factory=list)
    next_page_id: str | None = None

    @classmethod
    def from_data(cls, data) -> SharedLinksPage:
        return cls(
            items=[SharedLink.from_data(item) for item in safe_get(data, 0, default=[]) or []],
            next_page_id=safe_get(data, 1),
        )


@dataclass
class MediaItem:
    media_key: str
    thumbnail_url: str | None = None
    width: int | None = None
    height: int | None = None
    timestamp: int | None = None
    timezone_offset: int | None = None
    creation_timestamp: int | None = None
    dedup_key: str | None = None
    is_owned: bool | None = None
    is_live_photo: bool = False
    video_duration: int | None = None
    is_archived: bool = False

    @classmethod
    def from_library_data(cls, item_data) -> MediaItem:
        flags = safe_get(item_data, 7, default=[]) or []
        return cls(
            media_key=safe_get(item_data, 0),
            thumbnail_url=safe_get(item_data, 1, 0),
            width=safe_get(item_data, 1, 1),
            height=safe_get(item_data, 1, 2),
            timestamp=safe_get(item_data, 2),
            dedup_key=safe_get(item_data, 3),
            timezone_offset=safe_get(item_data, 4),
            creation_timestamp=safe_get(item_data, 5),
            is_owned=not any(isinstance(row, list) and 27 in row for row in flags),
            is_archived=bool(safe_get(item_data, 13)),
            video_duration=safe_get(item_data, -1, "76647426", 0),
            is_live_photo=bool(safe_get(item_data, -1, "146008172")),
        )

    @classmethod
    def from_album_data(cls, item_data) -> MediaItem:
        return cls(
            media_key=safe_get(item_data, 0),
            thumbnail_url=safe_get(item_data, 1, 0),
            width=safe_get(item_data, 1, 1),
            height=safe_get(item_data, 1, 2),
            timestamp=safe_get(item_data, 2),
            dedup_key=safe_get(item_data, 3),
            timezone_offset=safe_get(item_data, 4),
            creation_timestamp=safe_get(item_data, 5),
            video_duration=safe_get(item_data, -1, "76647426", 0),
            is_live_photo=bool(safe_get(item_data, -1, "146008172")),
        )


@dataclass
class LibraryPage:
    items: list[MediaItem] = field(default_factory=list)
    next_page_id: str | None = None
    last_item_timestamp: int | None = None

    @classmethod
    def from_timeline_data(cls, data) -> LibraryPage:
        last = safe_get(data, 2)
        return cls(
            items=[MediaItem.from_library_data(item) for item in safe_get(data, 0, default=[]) or []],
            next_page_id=safe_get(data, 1),
            last_item_timestamp=int(last) if last else None,
        )


@dataclass
class AlbumPage:
    items: list[MediaItem] = field(default_factory=list)
    next_page_id: str | None = None
    media_key: str | None = None
    title: str | None = None
    owner: Actor | None = None
    item_count: int | None = None
    auth_key: str | None = None

    @classmethod
    def from_data(cls, data) -> AlbumPage:
        return cls(
            items=[MediaItem.from_album_data(item) for item in safe_get(data, 1, default=[]) or []],
            next_page_id=safe_get(data, 2),
            media_key=safe_get(data, 3, 0),
            title=safe_get(data, 3, 1),
            owner=Actor.from_data(safe_get(data, 3, 5)),
            item_count=safe_get(data, 3, 21),
            auth_key=safe_get(data, 3, 19),
        )


@dataclass
class ItemInfo:
    media_key: str | None = None
    dedup_key: str | None = None
    timestamp: int | None = None
    timezone_offset: int | None = None
    creation_timestamp: int | None = None
    download_url: str | None = None
    download_original_url: str | None = None
    live_photo_video_download_url: str | None = None
    is_live_photo: bool = False
    video_duration: int | None = None

    @classmethod
    def from_data(cls, item_data) -> ItemInfo:
        known = {"15", "76647426", "146008172", "163238866", "225032867", "318563170", "525000000"}
        ext_obj = None
        row = safe_get(item_data, 0, default=[]) or []
        if isinstance(row, list):
            for value in row:
                if isinstance(value, dict) and known.intersection(value.keys()):
                    ext_obj = value
                    break
        live = safe_get(ext_obj, "146008172") if ext_obj else None
        return cls(
            media_key=safe_get(item_data, 0, 0),
            dedup_key=safe_get(item_data, 0, 3),
            timestamp=safe_get(item_data, 0, 2),
            timezone_offset=safe_get(item_data, 0, 4),
            creation_timestamp=safe_get(item_data, 0, 5),
            download_url=safe_get(item_data, 1),
            download_original_url=safe_get(item_data, 7),
            video_duration=safe_get(ext_obj, "76647426", 0) if ext_obj else None,
            is_live_photo=bool(live),
            live_photo_video_download_url=safe_get(live, 3) if live else None,
        )


@dataclass
class ItemInfoExt:
    media_key: str | None = None
    file_name: str | None = None
    timestamp: int | None = None
    timezone_offset: int | None = None
    size: int | None = None
    owner_name: str | None = None
    saved_to_your_photos: bool | None = None

    @classmethod
    def from_data(cls, item_data) -> ItemInfoExt:
        owner = Actor.from_data(safe_get(item_data, 0, 27, 3, 0) or safe_get(item_data, 0, 27, 4, 0))
        if not owner.actor_id:
            owner = Actor.from_data(safe_get(item_data, 0, 28))
        flags = safe_get(item_data, 0, 12, default=[]) or []
        return cls(
            media_key=safe_get(item_data, 0, 0),
            file_name=safe_get(item_data, 0, 2),
            timestamp=safe_get(item_data, 0, 3),
            timezone_offset=safe_get(item_data, 0, 4),
            size=safe_get(item_data, 0, 5),
            owner_name=owner.name,
            saved_to_your_photos=not any(isinstance(row, list) and 20 in row for row in flags),
        )


@dataclass
class BatchMediaInfo:
    media_key: str | None = None
    file_name: str | None = None
    timestamp: int | None = None
    timezone_offset: int | None = None
    creation_timestamp: int | None = None
    size: int | None = None

    @classmethod
    def from_data(cls, item_data) -> BatchMediaInfo:
        return cls(
            media_key=safe_get(item_data, 0),
            file_name=safe_get(item_data, 1, 3),
            timestamp=safe_get(item_data, 1, 6),
            timezone_offset=safe_get(item_data, 1, 7),
            creation_timestamp=safe_get(item_data, 1, 8),
            size=safe_get(item_data, 1, 9),
        )


@dataclass
class DownloadReady:
    file_name: str | None = None
    download_url: str | None = None
    download_size: int | None = None

    @classmethod
    def from_data(cls, item_data) -> DownloadReady:
        return cls(
            file_name=safe_get(item_data, 0, 0, 0, 2, 0, 0),
            download_url=safe_get(item_data, 0, 0, 0, 2, 0, 1),
            download_size=safe_get(item_data, 0, 0, 0, 2, 0, 2),
        )


def parse_rpc(rpc_id: str, data):
    if not data:
        return None
    match rpc_id:
        case "lcxiM":
            return LibraryPage.from_timeline_data(data)
        case "EzkLib":
            return LibraryPage(
                items=[MediaItem.from_library_data(item) for item in safe_get(data, 0, default=[]) or []],
                next_page_id=safe_get(data, 1),
            )
        case "Z5xsfc":
            return AlbumsPage.from_data(data)
        case "snAcKc":
            return AlbumPage.from_data(data)
        case "F2A0H":
            return SharedLinksPage.from_data(data)
        case "VrseUb":
            return ItemInfo.from_data(data)
        case "fDcn4b":
            return ItemInfoExt.from_data(data)
        case "EWgK9e":
            return [BatchMediaInfo.from_data(item) for item in safe_get(data, 0, 1, default=[]) or []]
        case "dnv2s":
            return DownloadReady.from_data(data)
        case "pLFTfd":
            return safe_get(data, 0)
        case _:
            return data
