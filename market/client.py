"""Async client for the warframe.market public API.

* Items + orders use the **v2** API (``/v2/items``, ``/v2/orders/item/{slug}``,
  ``/v2/item/{slug}/set``).
* Price history only exists on the legacy **v1** API
  (``/v1/items/{slug}/statistics``), so that one call still goes to v1.

Everything is rate-limited (~3 req/s, as the API asks), cached with a short
TTL, de-duplicated while in flight, and retried with backoff on 429/5xx.
The aiohttp session is passed in, so the bot's existing shared session
(and its User-Agent) is reused.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import aiohttp

log = logging.getLogger("warframebot.market")

API_V2 = "https://api.warframe.market/v2"
API_V1 = "https://api.warframe.market/v1"
SITE = "https://warframe.market"

PLATFORMS = ("pc", "ps4", "xbox", "switch", "mobile")
ONLINE_STATUSES = frozenset({"ingame", "online"})

# Sent per-request, so it overrides whatever User-Agent the shared session has
# (the bot's session carries the *wiki* client's UA). Pass user_agent=None to
# fall back to the session's.
DEFAULT_USER_AGENT = "WarframeBOT-market/0.1 (Discord bot; +https://github.com/0xsint/My-Warframe-Bot)"

ITEMS_TTL = 6 * 3600  # item catalogue changes rarely
SET_TTL = 6 * 3600
ORDERS_TTL = 60  # live orders: keep fresh
STATS_TTL = 600


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class MarketError(Exception):
    """Base class for all warframe.market failures."""


class MarketUnavailable(MarketError):
    """Network failure, rate limiting that never cleared, or a Cloudflare block."""


class ItemNotFound(MarketError):
    def __init__(self, query: str, suggestions: list["Item"] | None = None) -> None:
        super().__init__(query)
        self.query = query
        self.suggestions = suggestions or []


class AmbiguousItem(MarketError):
    def __init__(self, query: str, candidates: list["Item"]) -> None:
        super().__init__(query)
        self.query = query
        self.candidates = candidates


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class Item:
    id: str
    slug: str
    name: str
    tags: tuple[str, ...] = ()
    set_root: bool = False
    quantity_in_set: int = 1
    max_rank: int | None = None
    ducats: int | None = None
    vaulted: bool | None = None

    @property
    def url(self) -> str:
        return f"{SITE}/items/{self.slug}"

    @classmethod
    def from_api(cls, d: dict[str, Any], lang: str = "en") -> "Item":
        i18n = d.get("i18n") or {}
        loc = i18n.get(lang) or next(iter(i18n.values()), {}) or {}
        slug = d["slug"]
        return cls(
            id=str(d.get("id", slug)),
            slug=slug,
            name=loc.get("name") or slug.replace("_", " ").title(),
            tags=tuple(d.get("tags") or ()),
            set_root=bool(d.get("setRoot")),
            quantity_in_set=int(d.get("quantityInSet") or 1),
            max_rank=d.get("maxRank"),
            ducats=d.get("ducats"),
            vaulted=d.get("vaulted"),
        )


@dataclass(frozen=True, slots=True)
class Order:
    id: str
    type: str  # "buy" | "sell"
    platinum: int
    quantity: int
    rank: int | None
    status: str  # "ingame" | "online" | "offline" | "invisible"
    user: str
    reputation: int
    platform: str
    updated_at: str | None = None

    @property
    def online(self) -> bool:
        return self.status in ONLINE_STATUSES

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "Order | None":
        """Parse one order; returns None for hidden/malformed orders."""
        try:
            if d.get("visible") is False:
                return None
            user = d.get("user") or {}
            return cls(
                id=str(d.get("id", "")),
                type=str(d.get("type") or d.get("order_type")),
                platinum=int(d["platinum"]),
                quantity=max(int(d.get("quantity", 1) or 1), 1),
                rank=d.get("rank", d.get("mod_rank")),
                status=str(user.get("status", "offline")),
                user=str(user.get("ingameName") or user.get("ingame_name") or "?"),
                reputation=int(user.get("reputation") or 0),
                platform=str(user.get("platform") or d.get("platform") or ""),
                updated_at=d.get("updatedAt") or d.get("last_update"),
            )
        except (KeyError, TypeError, ValueError):
            return None


# --------------------------------------------------------------------------- #
# Item index (name -> slug lookup, fuzzy search for autocomplete)
# --------------------------------------------------------------------------- #
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    return _NON_ALNUM.sub(" ", text.lower()).strip()


class ItemIndex:
    def __init__(self, items: list[Item]) -> None:
        self.items = items
        self.by_slug = {i.slug: i for i in items}
        self.by_id = {i.id: i for i in items}
        self._norm = [(normalize(i.name), i) for i in items]
        self._by_norm: dict[str, Item] = {}
        for n, i in self._norm:
            self._by_norm.setdefault(n, i)

    def __len__(self) -> int:
        return len(self.items)

    def search(self, query: str, limit: int = 25) -> list[Item]:
        q = normalize(query)
        if not q:
            return []
        tokens = q.split()
        exact: list[Item] = []
        prefix: list[tuple[int, Item]] = []
        contains: list[tuple[int, Item]] = []
        for norm, item in self._norm:
            if norm == q:
                exact.append(item)
            elif norm.startswith(q):
                prefix.append((len(norm), item))
            elif all(t in norm for t in tokens):
                contains.append((len(norm), item))
        prefix.sort(key=lambda t: t[0])
        contains.sort(key=lambda t: t[0])
        out = exact + [i for _, i in prefix] + [i for _, i in contains]
        if len(out) < limit:  # typo tolerance only when needed (it's the slow path)
            names = [n for n, _ in self._norm]
            seen = {i.slug for i in out}
            for n in difflib.get_close_matches(q, names, n=limit, cutoff=0.6):
                item = self._by_norm[n]
                if item.slug not in seen:
                    out.append(item)
                    seen.add(item.slug)
        return out[:limit]

    def resolve(self, query: str) -> Item:
        """Turn user input (slug, exact name, or loose name) into one Item."""
        raw = query.strip()
        if raw in self.by_slug:
            return self.by_slug[raw]
        q = normalize(raw)
        if q in self._by_norm:
            return self._by_norm[q]
        slug_guess = q.replace(" ", "_")
        if slug_guess in self.by_slug:
            return self.by_slug[slug_guess]
        hits = self.search(raw, limit=6)
        if not hits:
            raise ItemNotFound(raw)
        if len(hits) == 1:
            return hits[0]
        # Several plausible matches: don't guess on a price check.
        norm_hits = [normalize(h.name) for h in hits]
        starts = [h for h, n in zip(hits, norm_hits) if n.startswith(q)]
        if len(starts) == 1:
            return starts[0]
        raise AmbiguousItem(raw, hits[:5])


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
_MISS = object()


class WarframeMarketClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        platform: str = "pc",
        language: str = "en",
        crossplay: bool = True,
        min_interval: float = 0.35,
        max_retries: int = 3,
        user_agent: str | None = DEFAULT_USER_AGENT,
    ) -> None:
        self._session = session
        self.platform = platform
        self.language = language
        self.crossplay = crossplay
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.user_agent = user_agent
        self._next_slot = 0.0
        self._cache: dict[Any, tuple[float, Any]] = {}
        self._inflight: dict[Any, asyncio.Future] = {}
        self._stale_index: ItemIndex | None = None

    # ---- plumbing --------------------------------------------------------- #
    def _reserve_slot(self) -> float:
        """Reserve the next request slot; returns seconds to wait for it."""
        now = time.monotonic()
        slot = max(now, self._next_slot)
        self._next_slot = slot + self.min_interval
        return slot - now

    def _cache_get(self, key: Any) -> Any:
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        self._cache.pop(key, None)
        return _MISS

    def _cache_set(self, key: Any, value: Any, ttl: float) -> None:
        if len(self._cache) > 1000:  # crude bound; entries are small
            now = time.monotonic()
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        self._cache[key] = (time.monotonic() + ttl, value)

    async def _cached(self, key: Any, ttl: float, fetch: Callable[[], Awaitable[Any]]) -> Any:
        hit = self._cache_get(key)
        if hit is not _MISS:
            return hit
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(fetch())
            self._inflight[key] = task

            def _done(t: asyncio.Future, k: Any = key) -> None:
                self._inflight.pop(k, None)
                if not t.cancelled():
                    t.exception()  # mark retrieved; callers see it via await

            task.add_done_callback(_done)
        # shield: one caller timing out (e.g. autocomplete) must not kill the
        # shared request that other callers are awaiting.
        value = await asyncio.shield(task)
        self._cache_set(key, value, ttl)
        return value

    async def _request(
        self, version: str, path: str, *, platform: str | None = None
    ) -> Any:
        base = API_V2 if version == "v2" else API_V1
        url = f"{base}{path}"
        headers = {
            "Accept": "application/json",
            "Language": self.language,
            "Platform": platform or self.platform,
            "Crossplay": "true" if self.crossplay else "false",
        }
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        last: str = "unknown error"
        for attempt in range(1, self.max_retries + 1):
            await asyncio.sleep(self._reserve_slot())
            try:
                async with self._session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 404:
                        raise MarketError("warframe.market has no data for that item (HTTP 404).")
                    if resp.status == 403:
                        try:
                            snippet = " ".join((await resp.text())[:300].split())
                        except Exception:  # noqa: BLE001 - diagnostics only
                            snippet = "<unreadable>"
                        log.error(
                            "HTTP 403 from %s | server=%s cf-ray=%s cf-mitigated=%s | body: %s",
                            url, resp.headers.get("Server"), resp.headers.get("CF-RAY"),
                            resp.headers.get("cf-mitigated"), snippet,
                        )
                        raise MarketUnavailable(
                            "warframe.market refused the request (HTTP 403). This is usually "
                            "bot protection blocking this IP or request; see the bot log for details."
                        )
                    if resp.status == 429 or resp.status >= 500:
                        last = f"HTTP {resp.status}"
                        retry_after = resp.headers.get("Retry-After", "")
                        delay = float(retry_after) if retry_after.isdigit() else 1.5 * attempt
                        log.warning("%s on %s (attempt %d); backing off %.1fs",
                                    last, path, attempt, delay)
                        await asyncio.sleep(min(delay, 30))
                        continue
                    if resp.status >= 400:
                        raise MarketError(f"warframe.market returned HTTP {resp.status}.")
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                log.warning("Network error on %s (attempt %d): %s", path, attempt, last)
                await asyncio.sleep(0.8 * attempt)
        raise MarketUnavailable(f"warframe.market is not responding ({last}).")

    @staticmethod
    def _unwrap(body: Any) -> Any:
        if isinstance(body, dict):
            if "data" in body:
                return body["data"]
            if "payload" in body:  # v1
                return body["payload"]
        return body

    # ---- public API ------------------------------------------------------- #
    async def load_items(self) -> ItemIndex:
        async def fetch() -> ItemIndex:
            raw = self._unwrap(await self._request("v2", "/items"))
            items = [Item.from_api(d, self.language) for d in raw if d.get("slug")]
            log.info("Loaded %d tradable items from warframe.market", len(items))
            return ItemIndex(items)

        try:
            index = await self._cached(("items", self.language), ITEMS_TTL, fetch)
        except MarketError:
            if self._stale_index is not None:
                log.warning("Item refresh failed; serving stale item index.")
                return self._stale_index
            raise
        self._stale_index = index
        return index

    async def get_orders(self, slug: str, platform: str | None = None) -> list[Order]:
        platform = platform or self.platform

        async def fetch() -> list[Order]:
            raw = self._unwrap(await self._request("v2", f"/orders/item/{slug}", platform=platform))
            return [o for o in (Order.from_api(d) for d in raw or []) if o is not None]

        return await self._cached(("orders", slug, platform), ORDERS_TTL, fetch)

    async def get_set(self, slug: str) -> list[Item]:
        """All items in the set containing ``slug`` (root + parts), or [] if none."""

        async def fetch() -> list[Item]:
            data = self._unwrap(await self._request("v2", f"/item/{slug}/set")) or {}
            return [Item.from_api(d, self.language) for d in data.get("items", [])]

        return await self._cached(("set", slug), SET_TTL, fetch)

    async def get_stats(self, slug: str, platform: str | None = None) -> dict[str, list[dict]]:
        """Closed-trade history: {"48hours": [...], "90days": [...]} (v1 endpoint)."""
        platform = platform or self.platform

        async def fetch() -> dict[str, list[dict]]:
            data = self._unwrap(await self._request("v1", f"/items/{slug}/statistics", platform=platform)) or {}
            closed = data.get("statistics_closed") or {}
            return {
                "48hours": closed.get("48hours") or closed.get("hours_48") or [],
                "90days": closed.get("90days") or closed.get("days_90") or [],
            }

        return await self._cached(("stats", slug, platform), STATS_TTL, fetch)
