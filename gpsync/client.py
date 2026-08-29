from __future__ import annotations

import json
import time
import urllib.parse
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any, Iterable, Iterator

import requests
from requests.adapters import HTTPAdapter, Retry

from gpsync.parser import (
    AlbumPage,
    AlbumsPage,
    BatchMediaInfo,
    DownloadReady,
    ItemInfo,
    ItemInfoExt,
    LibraryPage,
    SharedLinksPage,
    parse_rpc,
)
from gpsync.util import parse_wiz_global_data

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class SessionExpired(RuntimeError):
    """Google session cookies are missing or no longer valid."""


class PhotosApiError(RuntimeError):
    pass


def _new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
        }
    )
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[429, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class PhotosClient:
    """Client for the same PhotosUi/batchexecute RPCs that photos.google.com uses."""

    def __init__(self, cookies_txt: Path, account_index: int = 0, pause_s: float = 0.15) -> None:
        self.cookies_txt = Path(cookies_txt)
        self.account_index = account_index
        self.pause_s = pause_s
        self.base_path = f"/u/{account_index}/" if account_index else "/"
        self.session = _new_session()
        self._load_cookies()
        self.global_data = self._load_global_data()
        self.email = self.global_data.get("oPEP7c")

    def _load_cookies(self) -> None:
        if not self.cookies_txt.exists():
            raise SessionExpired(f"Cookies file not found: {self.cookies_txt}. Run: gpsync login")
        jar = MozillaCookieJar(str(self.cookies_txt))
        jar.load(ignore_discard=True, ignore_expires=True)
        self.session.cookies.update(jar)

    def _load_global_data(self) -> dict[str, Any]:
        response = self.session.get(f"https://photos.google.com{self.base_path}", timeout=30)
        if "accounts.google.com" in response.url:
            raise SessionExpired("Redirected to Google login. Re-run: gpsync login")
        if self.account_index and f"/u/{self.account_index}/" not in response.url:
            raise SessionExpired(
                f"account_index {self.account_index} not present in this cookie session "
                f"(landed on {response.url})"
            )
        try:
            data = parse_wiz_global_data(response.text)
        except ValueError as exc:
            raise SessionExpired(str(exc)) from exc
        for key in ("SNlM0e", "FdrFJe", "cfb2h", "Im6cmf"):
            if key not in data:
                raise SessionExpired(f"Missing {key} in Photos bootstrap. Re-run: gpsync login")
        return data

    def rpc(self, rpc_id: str, data: list[Any], *, parse: bool = True) -> Any:
        query = {
            "rpcids": rpc_id,
            "source-path": self.base_path,
            "f.sid": self.global_data["FdrFJe"],
            "bl": self.global_data["cfb2h"],
            "rt": "c",
        }
        body = {
            "f.req": json.dumps([[[rpc_id, json.dumps(data, separators=(",", ":")), None, "generic"]]], separators=(",", ":")),
            "at": self.global_data["SNlM0e"],
        }
        encoded = "&".join(f"{key}={urllib.parse.quote(value, safe='')}" for key, value in body.items())
        url = f"https://photos.google.com{self.global_data['Im6cmf']}/data/batchexecute"
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                response = self.session.post(url, data=encoded, params=query, timeout=60)
                response.raise_for_status()
                payload = _extract_wrb_payload(response.text)
                if parse:
                    return parse_rpc(rpc_id, payload)
                return payload
            except (requests.RequestException, PhotosApiError, json.JSONDecodeError) as exc:
                last_error = exc
                time.sleep(self.pause_s * attempt * 2)
        raise PhotosApiError(f"{rpc_id} failed after retries: {last_error}") from last_error

    def get_albums_page(self, page_id: str | None = None, page_size: int = 100) -> AlbumsPage:
        time.sleep(self.pause_s)
        return self.rpc("Z5xsfc", [page_id, None, None, None, 1, None, None, page_size, [2], 5]) or AlbumsPage()

    def iter_albums(self) -> Iterator:
        page_id = None
        while True:
            page = self.get_albums_page(page_id)
            yield from page.items
            if not page.next_page_id:
                break
            page_id = page.next_page_id

    def get_shared_links_page(self, page_id: str | None = None) -> SharedLinksPage:
        time.sleep(self.pause_s)
        return self.rpc("F2A0H", [page_id, None, 2, None, 3]) or SharedLinksPage()

    def iter_shared_links(self) -> Iterator:
        page_id = None
        while True:
            page = self.get_shared_links_page(page_id)
            yield from page.items
            if not page.next_page_id:
                break
            page_id = page.next_page_id

    def get_album_page(
        self,
        album_key: str,
        page_id: str | None = None,
        auth_key: str | None = None,
    ) -> AlbumPage:
        time.sleep(self.pause_s)
        return self.rpc("snAcKc", [album_key, page_id, None, auth_key]) or AlbumPage()

    def iter_album_items(self, album_key: str, auth_key: str | None = None) -> Iterator:
        page_id = None
        while True:
            page = self.get_album_page(album_key, page_id, auth_key)
            yield from page.items
            if not page.next_page_id:
                break
            page_id = page.next_page_id

    def get_library_page(
        self,
        timestamp: int | None = None,
        page_id: str | None = None,
        source: str = "both",
        page_size: int = 500,
    ) -> LibraryPage:
        source_code = {"library": 1, "archive": 2, "both": 3}.get(source, 3)
        time.sleep(self.pause_s)
        return self.rpc("lcxiM", [page_id, timestamp, page_size, None, 1, source_code]) or LibraryPage()

    def iter_library(self, source: str = "both") -> Iterator:
        page_id = None
        timestamp = None
        seen: set[str] = set()
        while True:
            page = self.get_library_page(timestamp=timestamp, page_id=page_id, source=source)
            new_items = 0
            for item in page.items:
                if item.media_key in seen:
                    continue
                seen.add(item.media_key)
                new_items += 1
                yield item
            if not page.next_page_id or new_items == 0:
                break
            page_id = page.next_page_id
            timestamp = page.last_item_timestamp

    def get_item_info(
        self,
        media_key: str,
        album_key: str | None = None,
        auth_key: str | None = None,
    ) -> ItemInfo:
        time.sleep(self.pause_s)
        return self.rpc("VrseUb", [media_key, None, auth_key, None, album_key])

    def get_item_info_ext(self, media_key: str, auth_key: str | None = None) -> ItemInfoExt:
        time.sleep(self.pause_s)
        return self.rpc("fDcn4b", [media_key, 1, auth_key, None, 1])

    def get_batch_media_info(self, media_keys: Iterable[str]) -> list[BatchMediaInfo]:
        keys = [[key] for key in media_keys]
        if not keys:
            return []
        time.sleep(self.pause_s)
        return self.rpc(
            "EWgK9e",
            [[[keys], [[*([None] * 24), [], *([None] * 10), []]]]],
        ) or []

    def get_download_url(self, media_keys: list[str], auth_key: str | None = None) -> str | None:
        time.sleep(self.pause_s)
        return self.rpc("pLFTfd", [media_keys, None, auth_key])

    def get_download_token(self, media_keys: list[str]) -> Any:
        time.sleep(self.pause_s)
        return self.rpc("yCLA7", [[[key] for key in media_keys]], parse=False)

    def check_download_token(self, token: str) -> DownloadReady:
        time.sleep(self.pause_s)
        return self.rpc("dnv2s", [[token]])


def _extract_wrb_payload(body: str) -> Any:
    lines = [line for line in body.split("\n") if "wrb.fr" in line]
    if not lines:
        raise PhotosApiError("No wrb.fr envelope in batchexecute response (session expired?)")
    parsed = json.loads(lines[0])
    payload = parsed[0][2]
    if not payload:
        raise PhotosApiError("Empty batchexecute payload")
    return json.loads(payload)
