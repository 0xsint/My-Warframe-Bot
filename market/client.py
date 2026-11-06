"""Async client for the public (read-only) warframe.market API.

* No account or API key is needed for anything in this file.
* warframe.market asks for at most 3 requests/second, so every call goes
  through one global limiter. Responses are cached briefly, and the big
  catalogues (item list, riven/lich/sister weapons, ...) for 6 hours.
* HTTP 429 and 5xx answers are retried once after a short pause.

Endpoints used (v2 unless noted):
    /items                          item index (names, slugs, ducats, vaulted, ...)
    /item/{slug}                    full item details (tax, MR, rarity, wiki link, ...)
    /item/{slug}/set                every part of the set an item belongs to
    /orders/item/{slug}             all orders for an item
    /orders/item/{slug}/top         best 5 online buy + sell orders (with rank filters)
    /orders/recent                  the 500 most recently posted orders
    /user/{slug}, /orders/user/{slug}   public profile + listings of a player
    /riven/weapons, /riven/attributes   riven catalogue (incl. disposition)
    /lich/weapons|ephemeras|quirks, /sister/weapons|ephemeras
    v1 /items/{slug}/statistics     48 hour and 90 day sales history
    v1 /auctions/search             riven / lich / sister contracts

Used by the Discord cogs and the FastAPI app, so they never disagree.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Callable, Optional, TypeVar

from core.fuzzy import _norm, _rank_items  # noqa: F401  (_norm is re-exported for market.riven)

from .models import (  # re-exported: older code imports these from market.client
    ASSET_BASE,
    ONLINE_STATUSES,
    CatalogEntry,
    Item,
    ItemDetail,
    Order,
    RivenAttribute,
    RivenAuction,
    RivenWeapon,
    StatPoint,
    UserProfile,
    WeaponAuction,
    parse_catalog_entry,
    parse_item,
    parse_item_detail,
    parse_order,
    parse_riven_auction,
    parse_riven_weapon,
    parse_stat_point,
    parse_user,
    parse_weapon_auction,
)

API_V2 = "https://api.warframe.market/v2"
API_V1 = "https://api.warframe.market/v1"
USER_AGENT = "WarframeBOT/2.0 (Discord bot; https://github.com/0xsint/My-Warframe-Bot)"

PLATFORMS = ("pc", "ps4", "xbox", "switch", "mobile")
CATALOG_TTL = 6 * 3600

T = TypeVar("T")

# old private names, kept so existing imports keep working
_parse_item = parse_item
_parse_order = parse_order
_parse_riven_auction = parse_riven_auction

__all__ = [
    "API_V1", "API_V2", "ASSET_BASE", "ONLINE_STATUSES", "PLATFORMS", "USER_AGENT",
    "CatalogEntry", "Item", "ItemDetail", "ItemNotFound", "MarketError", "Order",
    "RivenAttribute", "RivenAuction", "RivenWeapon", "StatPoint", "UserProfile",
    "WarframeMarketClient", "WeaponAuction",
]


class MarketError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ItemNotFound(MarketError):
    pass


# --------------------------------------------------------------------------- #
# Name search
# --------------------------------------------------------------------------- #
# In-game family names -> the prefix the API uses for those weapons.
_RIVEN_ALIASES = (
    ("sister of parvos", "coda"), ("sister", "coda"), ("parvos", "coda"),
    ("kuva lich", "kuva"), ("lich", "kuva"),
)


def _expand_riven_query(query: str) -> str:
    q = _norm(query)
    for alias, repl in _RIVEN_ALIASES:
        q = re.sub(rf"\b{alias}\b", repl, q)
    return q


def _data(payload: Any) -> Any:
    """v2 wraps everything in {"apiVersion", "data", "error"}."""
    return payload.get("data") if isinstance(payload, dict) else None


def _as_list(payload: Any) -> list:
    data = _data(payload)
    return data if isinstance(data, list) else []


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class WarframeMarketClient:
    """Create once, share everywhere. Pass an aiohttp.ClientSession you manage."""

    def __init__(self, session, *, platform: str = "pc", crossplay: bool = True,
                 language: str = "en", cache_ttl: float = 60.0, min_interval: float = 0.34,
                 retry_delay: float = 1.0):
        self.session = session
        self.platform = platform
        self.crossplay = crossplay
        self.language = language
        self.cache_ttl = cache_ttl
        self.retry_delay = retry_delay
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._catalogs: dict[str, tuple[float, list]] = {}
        self._catalog_locks: dict[str, asyncio.Lock] = {}

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
        for attempt in range(2):
            try:
                data = await self._fetch(url, params, headers)
                break
            except MarketError as exc:
                retryable = exc.status == 429 or (exc.status or 0) >= 500
                if attempt == 0 and retryable and not isinstance(exc, ItemNotFound):
                    await asyncio.sleep(self.retry_delay)
                    continue
                raise

        self._cache[key] = (time.monotonic(), data)
        if len(self._cache) > 500:                   # crude bound on memory
            oldest = sorted(self._cache, key=lambda k: self._cache[k][0])[:100]
            for k in oldest:
                self._cache.pop(k, None)
        return data

    async def _fetch(self, url: str, params: dict | None, headers: dict) -> Any:
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
                    return await resp.json()
            except MarketError:
                raise
            except asyncio.TimeoutError as exc:
                raise MarketError("warframe.market timed out") from exc
            except Exception as exc:                 # aiohttp.ClientError, bad JSON, ...
                raise MarketError(f"Could not reach warframe.market: {exc}") from exc

    async def _catalog(self, path: str, parser: Callable[[dict], Optional[T]]) -> list[T]:
        """A v2 list endpoint, parsed and cached for CATALOG_TTL."""
        lock = self._catalog_locks.setdefault(path, asyncio.Lock())
        async with lock:
            hit = self._catalogs.get(path)
            if hit and hit[1] and time.monotonic() - hit[0] < CATALOG_TTL:
                return hit[1]
            payload = await self._get(f"{API_V2}{path}", ttl=0)
            parsed = [x for x in (parser(r) for r in _as_list(payload) if isinstance(r, dict)) if x]
            if parsed:
                self._catalogs[path] = (time.monotonic(), parsed)
            return parsed or (hit[1] if hit else [])

    # ----------------------------------------------------------------- items
    async def _load_items(self) -> list[Item]:
        return await self._catalog("/items", parse_item)

    async def items(self) -> list[Item]:
        return await self._load_items()

    async def search_items(self, query: str, limit: int = 10) -> list[Item]:
        return _rank_items(await self._load_items(), query, limit)

    async def resolve_item(self, query_or_slug: str) -> Item:
        found = await self.search_items(query_or_slug, 1)
        if not found:
            raise ItemNotFound(f"No tradable item matches '{query_or_slug}'", 404)
        return found[0]

    async def item_by_id(self, item_id: str) -> Optional[Item]:
        for it in await self._load_items():
            if it.id == item_id:
                return it
        return None

    async def item_names_by_id(self) -> dict[str, Item]:
        return {it.id: it for it in await self._load_items() if it.id}

    async def find_exact(self, name: str) -> Optional[Item]:
        """The item whose name is exactly ``name`` (or ``name + ' Set'``), else None.
        Used to cross-link wiki pages to the market without false matches."""
        want = _norm(name)
        items = await self._load_items()
        for candidate in (want, want + " set"):
            for it in items:
                if _norm(it.name) == candidate:
                    return it
        return None

    async def get_item(self, slug: str) -> ItemDetail:
        try:
            payload = await self._get(f"{API_V2}/item/{slug}", ttl=3600)
        except ItemNotFound:
            payload = None
        detail = parse_item_detail(_data(payload) or {})
        if detail is None:
            raise ItemNotFound(f"warframe.market has no details for '{slug}'", 404)
        return detail

    async def get_set(self, slug: str) -> list[ItemDetail]:
        """Every part of the set ``slug`` belongs to, set item first.
        Items that aren't part of a set come back as a one-element list."""
        payload = await self._get(f"{API_V2}/item/{slug}/set", ttl=3600)
        data = _data(payload) or {}
        raw = data.get("items") if isinstance(data, dict) else data
        parts = [d for d in (parse_item_detail(r) for r in raw or [] if isinstance(r, dict)) if d]
        parts.sort(key=lambda d: (not d.set_root, d.item.name))
        return parts

    # ---------------------------------------------------------------- orders
    async def get_orders(self, slug: str, *, platform: str | None = None,
                         crossplay: bool | None = None) -> list[Order]:
        payload = await self._get(f"{API_V2}/orders/item/{slug}", platform=platform, crossplay=crossplay)
        raw = _data(payload) or []
        if isinstance(raw, dict):                    # some responses nest buy/sell
            raw = [*raw.get("sell", []), *raw.get("buy", [])]
        return [o for o in (parse_order(r) for r in raw) if o]

    async def get_top_orders(self, slug: str, *, rank: int | None = None, charges: int | None = None,
                             amber_stars: int | None = None, cyan_stars: int | None = None,
                             subtype: str | None = None, platform: str | None = None,
                             crossplay: bool | None = None) -> dict[str, list[Order]]:
        """Best 5 online sell and buy orders: {"sell": [...cheapest], "buy": [...highest]}."""
        params = {k: v for k, v in (("rank", rank), ("charges", charges), ("amberStars", amber_stars),
                                    ("cyanStars", cyan_stars), ("subtype", subtype)) if v is not None}
        payload = await self._get(f"{API_V2}/orders/item/{slug}/top", params=params or None,
                                  platform=platform, crossplay=crossplay)
        data = _data(payload) or {}
        out: dict[str, list[Order]] = {}
        for side in ("sell", "buy"):
            orders = [o for o in (parse_order(r) for r in data.get(side) or []) if o]
            orders.sort(key=lambda o: o.platinum, reverse=(side == "buy"))
            out[side] = orders
        return out

    async def get_recent_orders(self, *, platform: str | None = None,
                                crossplay: bool | None = None) -> list[Order]:
        """The most recently posted orders site-wide (up to 500)."""
        payload = await self._get(f"{API_V2}/orders/recent", platform=platform,
                                  crossplay=crossplay, ttl=120)
        return [o for o in (parse_order(r) for r in _as_list(payload)) if o]

    # ----------------------------------------------------------------- users
    async def get_user(self, name: str) -> UserProfile:
        slug = name.strip().lower()
        try:
            payload = await self._get(f"{API_V2}/user/{slug}", ttl=60)
        except ItemNotFound:
            payload = None
        user = parse_user(_data(payload) or {})
        if user is None:
            raise ItemNotFound(f"No warframe.market user called '{name}'", 404)
        return user

    async def get_user_orders(self, name: str, user: UserProfile | None = None) -> list[Order]:
        slug = name.strip().lower()
        payload = await self._get(f"{API_V2}/orders/user/{slug}", ttl=60)
        owner = {"ingameName": user.name, "status": user.status,
                 "reputation": user.reputation} if user else {"ingameName": name}
        return [o for o in (parse_order(r, owner=owner) for r in _as_list(payload)) if o]

    # --------------------------------------------------------------- history
    async def get_statistics(self, slug: str) -> dict[str, list[StatPoint]]:
        """Closed-trade statistics: {"48hours": [...], "90days": [...]} (v1, best effort).
        Raises MarketError on network trouble; returns empty lists when the
        endpoint answers with something unexpected."""
        payload = await self._get(f"{API_V1}/items/{slug}/statistics", ttl=600)
        closed = ((payload or {}).get("payload") or {}).get("statistics_closed") or {}
        return {k: [p for p in (parse_stat_point(r) for r in closed.get(k) or []) if p]
                for k in ("48hours", "90days")}

    async def get_history(self, slug: str) -> Optional[dict]:
        """48h sales summary (best effort: returns None on any problem)."""
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

    # --------------------------------------------------------------- rivens
    async def riven_weapon_catalog(self) -> list[RivenWeapon]:
        return await self._catalog("/riven/weapons", parse_riven_weapon)

    async def _load_riven_weapons(self) -> list[Item]:
        return [w.item for w in await self.riven_weapon_catalog()]

    async def riven_weapon_info(self, slug: str) -> Optional[RivenWeapon]:
        for w in await self.riven_weapon_catalog():
            if w.item.slug == slug:
                return w
        return None

    async def search_riven_weapons(self, query: str, limit: int = 10) -> list[Item]:
        """Riven-eligible weapons. Unlike the general item list, this includes
        Kuva Lich and Sister/Coda weapons."""
        return _rank_items(await self._load_riven_weapons(), _expand_riven_query(query), limit)

    async def resolve_riven_weapon(self, query: str) -> Item:
        found = await self.search_riven_weapons(query, 1)
        if not found:
            raise ItemNotFound(f"No riven weapon matches '{query}'", 404)
        return found[0]

    async def resolve_riven_weapons(self, query: str) -> list[Item]:
        """The weapon a full name points to, or every weapon in a family name ('kuva lich')."""
        found = await self.search_riven_weapons(query, 8)
        if not found:
            raise ItemNotFound(f"No riven weapon matches '{query}'", 404)
        q = _expand_riven_query(query)
        exact = [it for it in found if q in (_norm(it.name), _norm(it.slug))]
        return exact[:1] if exact else found

    async def riven_attribute_catalog(self) -> list[CatalogEntry]:
        return await self._catalog("/riven/attributes", parse_catalog_entry)

    async def get_riven_attributes(self) -> list[str]:
        """url_name slugs of every riven stat (e.g. 'critical_damage')."""
        return [a.slug for a in await self.riven_attribute_catalog()]

    async def get_riven_auctions(self, weapon_slug: str, *, platform: str | None = None,
                                 crossplay: bool | None = None) -> list[RivenAuction]:
        """Riven auctions for one weapon. Hidden/closed/non-riven entries are dropped."""
        data = await self._get(f"{API_V1}/auctions/search",
                               params={"type": "riven", "weapon_url_name": weapon_slug},
                               platform=platform, crossplay=crossplay)
        raw = ((data or {}).get("payload") or {}).get("auctions") or [] if isinstance(data, dict) else []
        return [a for a in (parse_riven_auction(r) for r in raw) if a]

    # ------------------------------------------------------- lich / sister
    async def weapon_catalog(self, kind: str) -> list[CatalogEntry]:
        """kind = "lich" | "sister"."""
        return await self._catalog(f"/{kind}/weapons", parse_catalog_entry)

    async def ephemera_catalog(self, kind: str) -> list[CatalogEntry]:
        return await self._catalog(f"/{kind}/ephemeras", parse_catalog_entry)

    async def quirk_catalog(self, kind: str = "lich") -> list[CatalogEntry]:
        return await self._catalog(f"/{kind}/quirks", parse_catalog_entry)

    async def search_weapons(self, kind: str, query: str, limit: int = 10) -> list[CatalogEntry]:
        return _rank_items(await self.weapon_catalog(kind), _expand_riven_query(query), limit)

    async def resolve_weapon(self, kind: str, query: str) -> CatalogEntry:
        found = await self.search_weapons(kind, query, 1)
        if not found:
            label = "Kuva Lich" if kind == "lich" else "Sister of Parvos"
            raise ItemNotFound(f"No {label} weapon matches '{query}'", 404)
        return found[0]

    async def get_weapon_auctions(self, kind: str, weapon_slug: str, *, element: str | None = None,
                                  ephemera: bool | None = None, platform: str | None = None,
                                  crossplay: bool | None = None) -> list[WeaponAuction]:
        params: dict[str, Any] = {"type": kind, "weapon_url_name": weapon_slug}
        if element:
            params["element"] = element
        if ephemera is not None:
            params["ephemera"] = "true" if ephemera else "false"
        data = await self._get(f"{API_V1}/auctions/search", params=params,
                               platform=platform, crossplay=crossplay)
        raw = ((data or {}).get("payload") or {}).get("auctions") or [] if isinstance(data, dict) else []
        return [a for a in (parse_weapon_auction(r) for r in raw) if a]
