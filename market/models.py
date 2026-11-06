"""Data classes for warframe.market, plus defensive parsers.

Every parser takes one raw JSON object and returns a model, or None when the
object is unusable. Field access always goes through .get() with fallbacks
for the older v1 spelling (``url_name``, ``ingame_name``, ...): the API has
changed shape before, and one missing key must not take a command down.

Nothing here does network I/O or imports Discord, so it is easy to test.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

SITE = "https://warframe.market"
ASSET_BASE = "https://warframe.market/static/assets/"
ONLINE_STATUSES = frozenset({"ingame", "online"})


def asset_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return path if path.startswith("http") else ASSET_BASE + path.lstrip("/")


def _en(raw: dict[str, Any]) -> dict[str, Any]:
    """The English i18n block (or the first language present)."""
    i18n = raw.get("i18n") or {}
    if not isinstance(i18n, dict) or not i18n:
        return {}
    return i18n.get("en") or next(iter(i18n.values()), {}) or {}


def _opt_int(value: Any) -> Optional[int]:
    try:
        return None if value is None or value == "" else int(value)
    except (TypeError, ValueError):
        return None


def _opt_float(value: Any) -> Optional[float]:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Items
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Item:
    """One tradable item from the item index (``/v2/items``)."""
    slug: str
    name: str
    icon: Optional[str] = None
    thumb: Optional[str] = None
    tags: tuple[str, ...] = ()
    max_rank: Optional[int] = None
    id: str = ""
    ducats: Optional[int] = None
    vaulted: Optional[bool] = None
    max_charges: Optional[int] = None
    max_amber_stars: Optional[int] = None
    max_cyan_stars: Optional[int] = None
    subtypes: tuple[str, ...] = ()

    @property
    def image_url(self) -> Optional[str]:
        return asset_url(self.icon or self.thumb)

    @property
    def thumb_url(self) -> Optional[str]:
        return asset_url(self.thumb or self.icon)

    @property
    def url(self) -> str:
        return f"{SITE}/items/{self.slug}"

    @property
    def is_set(self) -> bool:
        return "set" in self.tags or self.slug.endswith("_set")

    @property
    def is_prime(self) -> bool:
        return "prime" in self.tags or "prime" in self.slug.split("_")


def parse_item(raw: dict[str, Any]) -> Optional[Item]:
    slug = raw.get("slug") or raw.get("url_name")
    en = _en(raw)
    name = en.get("name") or raw.get("name") or raw.get("item_name") or slug
    if not slug or not name:
        return None
    return Item(
        slug=str(slug),
        name=str(name),
        icon=en.get("icon") or raw.get("icon"),
        thumb=en.get("thumb") or raw.get("thumb"),
        tags=tuple(raw.get("tags") or ()),
        max_rank=_opt_int(raw.get("maxRank", raw.get("mod_max_rank"))),
        id=str(raw.get("id") or ""),
        ducats=_opt_int(raw.get("ducats")),
        vaulted=raw.get("vaulted"),
        max_charges=_opt_int(raw.get("maxCharges")),
        max_amber_stars=_opt_int(raw.get("maxAmberStars")),
        max_cyan_stars=_opt_int(raw.get("maxCyanStars")),
        subtypes=tuple(raw.get("subtypes") or ()),
    )


@dataclass(frozen=True)
class ItemDetail:
    """Everything ``/v2/item/{slug}`` knows about one item."""
    item: Item
    description: str = ""
    wiki_url: Optional[str] = None
    rarity: Optional[str] = None
    trading_tax: Optional[int] = None
    mastery_rank: Optional[int] = None
    set_root: bool = False
    set_parts: tuple[str, ...] = ()         # item ids
    quantity_in_set: Optional[int] = None
    bulk_tradable: bool = False
    base_endo: Optional[int] = None
    endo_multiplier: Optional[float] = None
    vosfor: Optional[int] = None
    game_ref: str = ""

    @property
    def endo_at_max(self) -> Optional[int]:
        """Endo you get for dissolving a max-rank copy (base * (rank + 1) * mult)."""
        if self.base_endo is None or self.item.max_rank is None:
            return None
        mult = self.endo_multiplier or 1
        return int(self.base_endo * (self.item.max_rank + 1) * mult)


def parse_item_detail(raw: dict[str, Any]) -> Optional[ItemDetail]:
    item = parse_item(raw)
    if item is None:
        return None
    en = _en(raw)
    return ItemDetail(
        item=item,
        description=str(en.get("description") or "").strip(),
        wiki_url=en.get("wikiLink") or en.get("wiki_link"),
        rarity=raw.get("rarity"),
        trading_tax=_opt_int(raw.get("tradingTax", raw.get("trading_tax"))),
        mastery_rank=_opt_int(raw.get("reqMasteryRank", raw.get("mastery_level"))),
        set_root=bool(raw.get("setRoot", raw.get("set_root", False))),
        set_parts=tuple(str(p) for p in raw.get("setParts") or ()),
        quantity_in_set=_opt_int(raw.get("quantityInSet", raw.get("quantity_for_set"))),
        bulk_tradable=bool(raw.get("bulkTradable", False)),
        base_endo=_opt_int(raw.get("baseEndo")),
        endo_multiplier=_opt_float(raw.get("endoMultiplier")),
        vosfor=_opt_int(raw.get("vosfor")),
        game_ref=str(raw.get("gameRef") or ""),
    )


# --------------------------------------------------------------------------- #
# Orders and users
# --------------------------------------------------------------------------- #
@dataclass
class Order:
    id: str
    type: str                       # "sell" | "buy"
    platinum: int
    quantity: int
    rank: Optional[int]
    seller: str                     # in-game name of whoever posted it
    status: str                     # "ingame" | "online" | "offline" | "invisible"
    reputation: int = 0
    platform: str = ""
    crossplay: bool = False
    updated_at: str = ""
    item_id: str = ""
    per_trade: Optional[int] = None
    charges: Optional[int] = None
    subtype: Optional[str] = None
    amber_stars: Optional[int] = None
    cyan_stars: Optional[int] = None
    created_at: str = ""
    visible: bool = True

    @property
    def is_online(self) -> bool:
        return self.status in ONLINE_STATUSES


def parse_order(raw: dict[str, Any], *, owner: Optional[dict] = None) -> Optional[Order]:
    """``owner`` fills in the user for endpoints whose orders carry none
    (``/v2/orders/user/{slug}``)."""
    user = raw.get("user") or owner or {}
    plat = _opt_int(raw.get("platinum"))
    order_type = raw.get("type") or raw.get("order_type")
    if plat is None or not order_type:
        return None
    if raw.get("visible") is False:
        return None
    item = raw.get("item") if isinstance(raw.get("item"), dict) else {}
    return Order(
        id=str(raw.get("id", "")),
        type=str(order_type),
        platinum=plat,
        quantity=_opt_int(raw.get("quantity")) or 1,
        rank=_opt_int(raw.get("rank", raw.get("mod_rank"))),
        seller=str(user.get("ingameName") or user.get("ingame_name") or "unknown"),
        status=str(user.get("status") or "offline"),
        reputation=_opt_int(user.get("reputation")) or 0,
        platform=str(user.get("platform") or ""),
        crossplay=bool(user.get("crossplay", False)),
        updated_at=str(raw.get("updatedAt") or raw.get("last_update") or ""),
        item_id=str(raw.get("itemId") or item.get("id") or ""),
        per_trade=_opt_int(raw.get("perTrade")),
        charges=_opt_int(raw.get("charges")),
        subtype=raw.get("subtype"),
        amber_stars=_opt_int(raw.get("amberStars")),
        cyan_stars=_opt_int(raw.get("cyanStars")),
        created_at=str(raw.get("createdAt") or raw.get("creation_date") or ""),
    )


@dataclass(frozen=True)
class UserProfile:
    slug: str
    name: str
    reputation: int = 0
    status: str = "offline"
    platform: str = ""
    crossplay: bool = False
    locale: str = ""
    avatar: Optional[str] = None
    background: Optional[str] = None
    about: str = ""
    mastery_rank: Optional[int] = None
    last_seen: str = ""
    activity: str = ""
    banned: bool = False

    @property
    def url(self) -> str:
        return f"{SITE}/profile/{self.slug}"

    @property
    def avatar_url(self) -> Optional[str]:
        return asset_url(self.avatar)

    @property
    def is_online(self) -> bool:
        return self.status in ONLINE_STATUSES


def parse_user(raw: dict[str, Any]) -> Optional[UserProfile]:
    name = raw.get("ingameName") or raw.get("ingame_name")
    if not name:
        return None
    activity = raw.get("activity") or {}
    act = ""
    if isinstance(activity, dict):
        act = str(activity.get("details") or activity.get("type") or "")
        if act.upper() == "UNKNOWN":
            act = ""
    return UserProfile(
        slug=str(raw.get("slug") or name).lower(),
        name=str(name),
        reputation=_opt_int(raw.get("reputation")) or 0,
        status=str(raw.get("status") or "offline"),
        platform=str(raw.get("platform") or ""),
        crossplay=bool(raw.get("crossplay", False)),
        locale=str(raw.get("locale") or ""),
        avatar=raw.get("avatar"),
        background=raw.get("background"),
        about=str(raw.get("aboutRaw") or raw.get("about") or "").strip(),
        mastery_rank=_opt_int(raw.get("masteryRank", raw.get("mastery_level"))),
        last_seen=str(raw.get("lastSeen") or raw.get("last_seen") or ""),
        activity=act,
        banned=bool(raw.get("banned", False)),
    )


# --------------------------------------------------------------------------- #
# Riven / Lich / Sister catalogues
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RivenWeapon:
    item: Item
    group: str = ""                 # "primary" | "secondary" | "melee" | ...
    riven_type: str = ""
    disposition: Optional[float] = None
    mastery_rank: Optional[int] = None

    @property
    def disposition_dots(self) -> str:
        """The in-game 1-5 dot display (thresholds as used by the wiki)."""
        d = self.disposition
        if d is None:
            return "?"
        dots = 1 if d < 0.7 else 2 if d < 0.9 else 3 if d <= 1.1 else 4 if d <= 1.3 else 5
        return "●" * dots + "○" * (5 - dots)


def parse_riven_weapon(raw: dict[str, Any]) -> Optional[RivenWeapon]:
    item = parse_item(raw)
    if item is None:
        return None
    return RivenWeapon(
        item=item,
        group=str(raw.get("group") or ""),
        riven_type=str(raw.get("rivenType") or raw.get("riven_type") or ""),
        disposition=_opt_float(raw.get("disposition")),
        mastery_rank=_opt_int(raw.get("reqMasteryRank")),
    )


@dataclass(frozen=True)
class CatalogEntry:
    """A lich/sister weapon, ephemera, quirk, riven attribute, mission, NPC, ..."""
    slug: str
    name: str
    icon: Optional[str] = None
    element: Optional[str] = None
    group: Optional[str] = None
    description: str = ""
    extra: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def image_url(self) -> Optional[str]:
        return asset_url(self.icon)


def parse_catalog_entry(raw: dict[str, Any]) -> Optional[CatalogEntry]:
    slug = raw.get("slug") or raw.get("url_name")
    en = _en(raw)
    name = en.get("name") or raw.get("item_name") or raw.get("effect") or slug
    if not slug or not name:
        return None
    known = {"slug", "url_name", "i18n", "element", "group", "id"}
    return CatalogEntry(
        slug=str(slug), name=str(name), icon=en.get("icon") or en.get("thumb"),
        element=raw.get("element"), group=raw.get("group"),
        description=str(en.get("description") or "").strip(),
        extra={k: v for k, v in raw.items() if k not in known},
    )


# --------------------------------------------------------------------------- #
# Auctions (riven / lich / sister contracts)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RivenAttribute:
    url_name: str                   # e.g. "critical_damage"
    value: float                    # percent, already signed
    positive: bool                  # True when the stat is a buff on this riven


@dataclass
class RivenAuction:
    """One riven 'contract' (auction) from warframe.market."""
    id: str
    weapon: str                     # weapon slug, e.g. "acceltra"
    name: str                       # riven name, e.g. "sati-acritox"
    mod_rank: int                   # 0-8
    re_rolls: int
    polarity: str                   # "madurai" | "vazarin" | "naramon" | ""
    mastery_level: Optional[int]
    attributes: tuple[RivenAttribute, ...]
    buyout_price: Optional[int]
    starting_price: Optional[int]
    top_bid: Optional[int]
    seller: str
    status: str
    platform: str = ""

    @property
    def is_online(self) -> bool:
        return self.status in ONLINE_STATUSES

    @property
    def url(self) -> str:
        return f"{SITE}/auction/{self.id}"


def parse_riven_auction(raw: dict[str, Any]) -> Optional[RivenAuction]:
    item = raw.get("item") or {}
    if item.get("type") != "riven" or raw.get("visible") is False or raw.get("closed"):
        return None
    owner = raw.get("owner") or {}
    attrs = tuple(
        RivenAttribute(url_name=str(a["url_name"]), value=float(a.get("value") or 0),
                       positive=bool(a.get("positive")))
        for a in item.get("attributes") or [] if a.get("url_name"))
    return RivenAuction(
        id=str(raw.get("id", "")),
        weapon=str(item.get("weapon_url_name") or ""),
        name=str(item.get("name") or ""),
        mod_rank=_opt_int(item.get("mod_rank")) or 0,
        re_rolls=_opt_int(item.get("re_rolls")) or 0,
        polarity=str(item.get("polarity") or "").lower(),
        mastery_level=_opt_int(item.get("mastery_level")),
        attributes=attrs,
        buyout_price=_opt_int(raw.get("buyout_price")),
        starting_price=_opt_int(raw.get("starting_price")),
        top_bid=_opt_int(raw.get("top_bid")),
        seller=str(owner.get("ingame_name") or owner.get("ingameName") or "unknown"),
        status=str(owner.get("status") or "offline"),
        platform=str(raw.get("platform") or owner.get("platform") or ""),
    )


@dataclass
class WeaponAuction:
    """A Kuva Lich or Sister of Parvos weapon contract."""
    id: str
    kind: str                       # "lich" | "sister"
    weapon: str                     # weapon slug, e.g. "kuva_bramma"
    element: str                    # "heat", "toxin", ...
    damage: int                     # bonus %, 25-60
    ephemera: bool
    quirk: Optional[str]
    name: str
    buyout_price: Optional[int]
    starting_price: Optional[int]
    top_bid: Optional[int]
    seller: str
    status: str
    platform: str = ""

    @property
    def is_online(self) -> bool:
        return self.status in ONLINE_STATUSES

    @property
    def url(self) -> str:
        return f"{SITE}/auction/{self.id}"


def parse_weapon_auction(raw: dict[str, Any]) -> Optional[WeaponAuction]:
    item = raw.get("item") or {}
    kind = item.get("type")
    if kind not in ("lich", "sister") or raw.get("visible") is False or raw.get("closed"):
        return None
    owner = raw.get("owner") or {}
    return WeaponAuction(
        id=str(raw.get("id", "")),
        kind=str(kind),
        weapon=str(item.get("weapon_url_name") or ""),
        element=str(item.get("element") or "").lower(),
        damage=_opt_int(item.get("damage")) or 0,
        ephemera=bool(item.get("having_ephemera", item.get("ephemera", False))),
        quirk=item.get("quirk") or None,
        name=str(item.get("name") or ""),
        buyout_price=_opt_int(raw.get("buyout_price")),
        starting_price=_opt_int(raw.get("starting_price")),
        top_bid=_opt_int(raw.get("top_bid")),
        seller=str(owner.get("ingame_name") or owner.get("ingameName") or "unknown"),
        status=str(owner.get("status") or "offline"),
        platform=str(raw.get("platform") or owner.get("platform") or ""),
    )


def asking_price(auction) -> Optional[int]:
    """What a buyer pays for any auction: the buyout when set, else the
    current top bid (or the starting bid when nobody has bid yet)."""
    if auction.buyout_price:
        return auction.buyout_price
    return auction.top_bid or auction.starting_price or None


# --------------------------------------------------------------------------- #
# Price history (v1 statistics)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StatPoint:
    when: str                       # ISO datetime of the bucket
    volume: int
    min_price: Optional[float]
    max_price: Optional[float]
    avg_price: Optional[float]
    median: Optional[float]
    rank: Optional[int] = None


def parse_stat_point(raw: dict[str, Any]) -> Optional[StatPoint]:
    when = raw.get("datetime") or raw.get("date")
    if not when:
        return None
    return StatPoint(
        when=str(when),
        volume=_opt_int(raw.get("volume")) or 0,
        min_price=_opt_float(raw.get("min_price")),
        max_price=_opt_float(raw.get("max_price")),
        avg_price=_opt_float(raw.get("avg_price")),
        median=_opt_float(raw.get("median")),
        rank=_opt_int(raw.get("mod_rank")),
    )
