"""Small async client for the warframe.market public API (v2).

* No API key needed for read-only endpoints.
* warframe.market asks for <= 3 requests/second, so every call goes through a
  global limiter, and responses are cached for a short time.
* Field access is defensive (.get with fallbacks): the API has changed shape
  before, and one missing key should not take the whole command down.

Used by both the Discord cog (cogs/market_commands.py) and the FastAPI app
(api/main.py), so the two never disagree about the numbers.
"""
from __future__ import annotations

import asyncio
import difflib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

API_V2 = "https://api.warframe.market/v2"
API_V1 = "https://api.warframe.market/v1"
ASSET_BASE = "https://warframe.market/static/assets/"
USER_AGENT = "WarframeBOT/1.0 (Discord bot; https://github.com/0xsint/My-Warframe-Bot)"

PLATFORMS = ("pc", "ps4", "xbox", "switch")
ONLINE_STATUSES = {"ingame", "online"}


class MarketError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ItemNotFound(MarketError):
    pass


@dataclass(frozen=True)
class Item:
    slug: str
    name: str
    icon: Optional[str] = None
    thumb: Optional[str] = None
    tags: tuple[str, ...] = ()
    max_rank: Optional[int] = None

    @property
    def image_url(self) -> Optional[str]:
        path = self.icon or self.thumb
        return ASSET_BASE + path.lstrip("/") if path else None

    @property
    def url(self) -> str:
        return f"https://warframe.market/items/{self.slug}"


@dataclass
class Order:
    id: str
    type: str                       # "sell" | "buy"
    platinum: int
    quantity: int
    rank: Optional[int]
    seller: str                     # in-game name
    status: str                     # "ingame" | "online" | "offline"
    reputation: int = 0
    platform: str = ""
    crossplay: bool = False
    updated_at: str = ""

    @property
    def is_online(self) -> bool:
        return self.status in ONLINE_STATUSES


def _parse_order(raw: dict[str, Any]) -> Optional[Order]:
    user = raw.get("user") or {}
    plat = raw.get("platinum")
    if plat is None or not raw.get("type"):
        return None
    if raw.get("visible") is False:
        return None
    return Order(
        id=str(raw.get("id", "")),
        type=str(raw["type"]),
        platinum=int(plat),
        quantity=int(raw.get("quantity") or 1),
        rank=raw.get("rank"),
        seller=str(user.get("ingameName") or user.get("ingame_name") or "unknown"),
        status=str(user.get("status") or "offline"),
        reputation=int(user.get("reputation") or 0),
        platform=str(user.get("platform") or ""),
        crossplay=bool(user.get("crossplay", False)),
        updated_at=str(raw.get("updatedAt") or raw.get("last_update") or ""),
    )


def _parse_item(raw: dict[str, Any]) -> Optional[Item]:
    slug = raw.get("slug") or raw.get("url_name")
    i18n = raw.get("i18n") or {}
    en = i18n.get("en") or next(iter(i18n.values()), {}) if i18n else {}
    name = en.get("name") or raw.get("name") or raw.get("item_name") or slug
    if not slug or not name:
        return None
    return Item(
        slug=slug,
        name=name,
        icon=en.get("icon"),
        thumb=en.get("thumb"),
        tags=tuple(raw.get("tags") or ()),
        max_rank=raw.get("maxRank"),
    )


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


class WarframeMarketClient:
    """Create once, share everywhere. Pass an aiohttp.ClientSession you manage."""

    def __init__(self, session, *, platform: str = "pc", crossplay: bool = True,
                 language: str = "en", cache_ttl: float = 60.0, min_interval: float = 0.34):
        self.session = session
        self.platform = platform
        self.crossplay = crossplay
        self.language = language
        self.cache_ttl = cache_ttl
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._items: list[Item] = []
        self._items_loaded = 0.0
        self._items_lock = asyncio.Lock()

    # ------------------------------------------------------------- transport
    async def _get(self, url: str, *, params: dict | None = None, platform: str | None = None,
                   crossplay: bool | None = None, ttl: float | None = None) -> Any:
        platform = platform or self.platform
        crossplay = self.crossplay if crossplay is None else crossplay
        key = (url, tuple(sorted((params or {}).items())), platform, crossplay)
        ttl = self.cache_ttl if ttl is None else ttl
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]

        headers = {
            "User-Agent": USER_AGENT, "Accept": "application/json",
            "Language": self.language, "Platform": platform,
            "Crossplay": "true" if crossplay else "false",
        }
        async with self._lock:                       # global rate limit
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with self.session.get(url, params=params, headers=headers) as resp:
                    self._last_call = time.monotonic()
                    if resp.status == 404:
                        raise ItemNotFound("Not found on warframe.market", 404)
                    if resp.status == 429:
                        raise MarketError("warframe.market rate limit hit, try again in a moment", 429)
                    if resp.status >= 400:
                        raise MarketError(f"warframe.market returned HTTP {resp.status}", resp.status)
                    data = await resp.json()
            except MarketError:
                raise
            except asyncio.TimeoutError as exc:
                raise MarketError("warframe.market timed out") from exc
            except Exception as exc:                 # aiohttp.ClientError etc.
                raise MarketError(f"Could not reach warframe.market: {exc}") from exc

        self._cache[key] = (time.monotonic(), data)
        if len(self._cache) > 500:                   # crude bound on memory
            oldest = sorted(self._cache, key=lambda k: self._cache[k][0])[:100]
            for k in oldest:
                self._cache.pop(k, None)
        return data

    # ----------------------------------------------------------------- items
    async def _load_items(self) -> list[Item]:
        async with self._items_lock:
            if self._items and time.monotonic() - self._items_loaded < 6 * 3600:
                return self._items
            data = await self._get(f"{API_V2}/items", ttl=0)
            items = [i for i in (_parse_item(r) for r in data.get("data", [])) if i]
            if items:
                self._items, self._items_loaded = items, time.monotonic()
            return self._items

    async def search_items(self, query: str, limit: int = 10) -> list[Item]:
        items = await self._load_items()
        q = _norm(query)
        if not q:
            return items[:limit]
        scored: list[tuple[float, Item]] = []
        for it in items:
            n = _norm(it.name)
            if n == q or _norm(it.slug) == q:
                score = 100
            elif n.startswith(q):
                score = 80 - min(len(n) - len(q), 40) / 2
            elif q in n:
                score = 60 - min(n.index(q), 30) / 2
            elif all(tok in n for tok in q.split()):
                score = 50
            else:
                continue
            scored.append((score, it))
        scored.sort(key=lambda s: (-s[0], len(s[1].name)))
        results = [it for _, it in scored[:limit]]
        if not results:                              # typo tolerance
            names = {_norm(it.name): it for it in items}
            for m in difflib.get_close_matches(q, list(names), n=limit, cutoff=0.6):
                results.append(names[m])
        return results

    async def resolve_item(self, query_or_slug: str) -> Item:
        found = await self.search_items(query_or_slug, 1)
        if not found:
            raise ItemNotFound(f"No tradable item matches '{query_or_slug}'", 404)
        return found[0]

    # ---------------------------------------------------------------- orders
    async def get_orders(self, slug: str, *, platform: str | None = None,
                         crossplay: bool | None = None) -> list[Order]:
        data = await self._get(f"{API_V2}/orders/item/{slug}", platform=platform, crossplay=crossplay)
        raw = data.get("data", [])
        if isinstance(raw, dict):                    # some responses nest buy/sell
            raw = [*raw.get("sell", []), *raw.get("buy", [])]
        return [o for o in (_parse_order(r) for r in raw) if o]

    async def get_history(self, slug: str) -> Optional[dict]:
        """48h sales summary from the v1 statistics endpoint (best effort: this
        endpoint is older and may change; returns None on any problem)."""
        try:
            data = await self._get(f"{API_V1}/items/{slug}/statistics", ttl=300)
            rows = data["payload"]["statistics_closed"]["48hours"]
        except Exception:
            return None
        if not rows:
            return None
        volume = sum(int(r.get("volume") or 0) for r in rows)
        if volume == 0:
            return {"volume_48h": 0, "avg_price_48h": None, "median_48h": None}
        weighted = sum(float(r.get("avg_price") or 0) * int(r.get("volume") or 0) for r in rows)
        medians = sorted(float(r["median"]) for r in rows if r.get("median") is not None)
        return {
            "volume_48h": volume,
            "avg_price_48h": round(weighted / volume, 1),
            "median_48h": medians[len(medians) // 2] if medians else None,
        }
