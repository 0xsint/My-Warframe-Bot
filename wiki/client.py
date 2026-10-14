"""Async HTTP client for the WARFRAME Wiki's MediaWiki API.

The WARFRAME Wiki (wiki.warframe.com) runs on MediaWiki, so instead of
scraping rendered page HTML blindly we lean on the built-in ``api.php``
endpoints:

* ``action=opensearch``  -> fuzzy title search / autocomplete suggestions.
* ``action=parse``       -> fully rendered HTML for a page, which we then
  hand off to :mod:`wiki.parser` to pull structured infobox data out of.

A small in-memory TTL cache is used so that rapid repeated lookups (e.g. a
user re-running the same command, or autocomplete firing on every keystroke)
don't hit the wiki more than necessary.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp

from .parser import WikiPage, parse_page

log = logging.getLogger("warframebot.wiki.client")

API_URL = "https://wiki.warframe.com/api.php"
BASE_URL = "https://wiki.warframe.com"
USER_AGENT = (
    "WarframeBOT/1.0 (+https://wiki.warframe.com user-installed Discord app; "
    "contact: bot owner via Discord) Python-aiohttp"
)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)


class WikiError(Exception):
    """Raised when the WARFRAME Wiki can't be reached or returns an error."""


class PageNotFoundError(WikiError):
    """Raised when a requested page does not exist on the wiki."""


@dataclass(slots=True)
class _CacheEntry:
    value: Any
    expires_at: float


class WarframeWikiClient:
    """Thin async wrapper around the WARFRAME Wiki's MediaWiki API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        search_cache_ttl: float = 300.0,
        page_cache_ttl: float = 900.0,
        max_concurrent_requests: int = 4,
    ) -> None:
        self._session = session
        self._search_cache_ttl = search_cache_ttl
        self._page_cache_ttl = page_cache_ttl
        self._cache: dict[str, _CacheEntry] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)

    # -- caching helpers -------------------------------------------------

    def _get_cached(self, key: str) -> Any:
        entry = self._cache.get(key)
        if entry is not None and entry.expires_at > time.monotonic():
            return entry.value
        if entry is not None:
            self._cache.pop(key, None)
        return None

    def _set_cached(self, key: str, value: Any, ttl: float) -> None:
        self._cache[key] = _CacheEntry(value, time.monotonic() + ttl)
        print(key)

    # -- low level request -------------------------------------------------

    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        query = {**params, "format": "json", "formatversion": "2"}
        async with self._semaphore:
            try:
                async with self._session.get(
                    API_URL,
                    params=query,
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                    timeout=REQUEST_TIMEOUT,
                ) as resp:
                    if resp.status == 403:
                        raise WikiError(
                            "wiki.warframe.com rejected this request with HTTP 403 "
                            "(usually a Cloudflare bot challenge triggered by the "
                            "server's IP address). This tends to happen on cloud/"
                            "datacenter hosts; running the bot from a residential "
                            "network/home PC typically avoids it."
                        )
                    if resp.status == 429:
                        raise WikiError(
                            "wiki.warframe.com is rate-limiting this bot right now. "
                            "Please try again in a moment."
                        )
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
            except aiohttp.ClientError as exc:
                raise WikiError(f"Could not reach the WARFRAME Wiki: {exc}") from exc
            except asyncio.TimeoutError as exc:
                raise WikiError("The WARFRAME Wiki took too long to respond.") from exc

    # -- public API -------------------------------------------------------

    async def search(self, query: str, limit: int = 10) -> list[tuple[str, str]]:
        """Return up to ``limit`` ``(title, url)`` matches for ``query``."""
        query = query.strip()
        if not query:
            return []

        cache_key = f"search:{limit}:{query.lower()}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        data = await self._request(
            {
                "action": "opensearch",
                "search": query,
                "limit": limit,
                "namespace": 0,
                "redirects": "resolve",
            }
        )
        titles = data[1] if len(data) > 1 else []
        urls = data[3] if len(data) > 3 else []
        results = list(zip(titles, urls))
        self._set_cached(cache_key, results, self._search_cache_ttl)
        return results

    async def resolve_title(self, query: str) -> Optional[str]:
        """Resolve a (possibly partial/misspelled) query to a real page title."""
        results = await self.search(query, limit=1)
        return results[0][0] if results else None

    async def get_page(self, title: str) -> WikiPage:
        """Fetch and parse a page by its *exact* title (redirects are followed)."""
        cache_key = f"page:{title.lower()}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        data = await self._request(
            {
                "action": "parse",
                "page": title,
                "prop": "text",
                "redirects": 1,
            }
        )
        if "error" in data:
            code = data["error"].get("code", "")
            if code in ("missingtitle", "nosuchsection"):
                raise PageNotFoundError(f'No WARFRAME Wiki page named "{title}" was found.')
            raise WikiError(data["error"].get("info", "Unknown WARFRAME Wiki API error."))

        parsed = data["parse"]
        resolved_title = parsed["title"]
        html = parsed["text"]
        url = f"{BASE_URL}/w/{resolved_title.replace(' ', '_')}"

        page = parse_page(resolved_title, url, html)
        self._set_cached(cache_key, page, self._page_cache_ttl)
        return page

    async def fetch(self, query: str) -> WikiPage:
        """Resolve a user-typed ``query`` to a page and return its parsed content.

        First tries an opensearch lookup (handles typos/partial names/aliases),
        falling back to treating ``query`` as an exact title.
        """
        query = query.strip()
        if not query:
            raise PageNotFoundError("Please provide a page name to look up.")

        title = await self.resolve_title(query)
        try:
            return await self.get_page(title or query)
        except PageNotFoundError:
            if title and title.lower() != query.lower():
                raise
            # Last resort: try the raw query directly in case opensearch missed it.
            return await self.get_page(query)
