"""Market analysis beyond a single item's order book.

    set_report()        is a set worth more than its parts? (buy parts -> sell set)
    history_summary()   90-day / 48-hour price history, trend, and a sparkline
    ducat_deals()       best ducats-per-platinum among recently listed prime parts
    recent_trends()     busiest items among the latest orders posted site-wide
    filter_weapon_auctions()   Kuva Lich / Sister of Parvos weapon contracts

Pure functions only (no network, no Discord), like market/analysis.py.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Iterable, Mapping, Optional, Sequence

from .models import Item, ItemDetail, Order, StatPoint, WeaponAuction, asking_price

SPARK = "▁▂▃▄▅▆▇█"


def sparkline(values: Sequence[Optional[float]]) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    span = hi - lo or 1
    return "".join(" " if v is None else SPARK[int((v - lo) / span * (len(SPARK) - 1))] for v in values)


def _best(orders: Iterable[Order], side: str) -> Optional[int]:
    prices = [o.platinum for o in orders if o.type == side and o.is_online]
    if not prices:
        return None
    return min(prices) if side == "sell" else max(prices)


# --------------------------------------------------------------------------- #
# Sets
# --------------------------------------------------------------------------- #
@dataclass
class SetPart:
    name: str
    slug: str
    quantity: int
    lowest_sell: Optional[int]
    highest_buy: Optional[int]
    ducats: Optional[int]

    @property
    def sell_total(self) -> Optional[int]:
        return None if self.lowest_sell is None else self.lowest_sell * self.quantity

    @property
    def buy_total(self) -> Optional[int]:
        return None if self.highest_buy is None else self.highest_buy * self.quantity


@dataclass
class SetReport:
    set_name: str
    set_slug: str
    set_lowest_sell: Optional[int]
    set_highest_buy: Optional[int]
    parts: list[SetPart] = field(default_factory=list)
    parts_lowest_sell: Optional[int] = None     # buy every part from the cheapest seller
    parts_highest_buy: Optional[int] = None     # sell every part to the best buyer
    total_ducats: int = 0
    verdict: str = ""

    @property
    def flip_profit(self) -> Optional[int]:
        """Buy all parts at the cheapest asks, sell the set at its cheapest ask."""
        if self.set_lowest_sell is None or self.parts_lowest_sell is None:
            return None
        return self.set_lowest_sell - self.parts_lowest_sell

    def to_dict(self) -> dict:
        d = asdict(self)
        d["flip_profit"] = self.flip_profit
        return d


def set_report(parts: Sequence[ItemDetail], orders_by_slug: Mapping[str, Sequence[Order]]) -> SetReport:
    """``parts`` from client.get_set() (set item first); ``orders_by_slug`` maps
    each slug to its orders (top orders are enough)."""
    root = next((p for p in parts if p.set_root), parts[0] if parts else None)
    if root is None:
        raise ValueError("set_report needs at least one item")
    root_orders = orders_by_slug.get(root.item.slug, [])
    r = SetReport(set_name=root.item.name, set_slug=root.item.slug,
                  set_lowest_sell=_best(root_orders, "sell"), set_highest_buy=_best(root_orders, "buy"))
    for p in parts:
        if p is root:
            continue
        o = orders_by_slug.get(p.item.slug, [])
        r.parts.append(SetPart(name=p.item.name, slug=p.item.slug, quantity=p.quantity_in_set or 1,
                               lowest_sell=_best(o, "sell"), highest_buy=_best(o, "buy"),
                               ducats=p.item.ducats))
    r.total_ducats = sum((p.ducats or 0) * p.quantity for p in r.parts)
    if r.parts and all(p.sell_total is not None for p in r.parts):
        r.parts_lowest_sell = sum(p.sell_total for p in r.parts)
    if r.parts and all(p.buy_total is not None for p in r.parts):
        r.parts_highest_buy = sum(p.buy_total for p in r.parts)
    r.verdict = _set_verdict(r)
    return r


def _set_verdict(r: SetReport) -> str:
    if not r.parts:
        return "This item isn't part of a set."
    profit = r.flip_profit
    if profit is None:
        return "Not enough online sellers to compare the set with its parts."
    if profit >= max(5, 0.1 * (r.set_lowest_sell or 0)):
        return f"Buying the parts and selling the set earns about {profit}p."
    if profit <= -5:
        return f"Parts sell for {-profit}p more than the set: split it up and sell parts."
    return "Set and parts are priced about the same."


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
@dataclass
class HistorySummary:
    period: str                        # "90days" | "48hours"
    points: int
    volume: int
    avg_price: Optional[float]         # volume-weighted
    median_price: Optional[float]
    min_price: Optional[float]
    max_price: Optional[float]
    first_price: Optional[float]
    last_price: Optional[float]
    change_pct: Optional[float]        # recent window vs the window before it
    sparkline: str = ""
    ranks: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def history_summary(points: Sequence[StatPoint], *, period: str = "90days",
                    rank: Optional[int] = None, window: int = 7, spark_width: int = 30) -> HistorySummary:
    ranks = sorted({p.rank for p in points if p.rank is not None})
    if rank is not None:
        points = [p for p in points if p.rank == rank]
    elif ranks:                                    # mixed ranks: default to the lowest (unranked)
        points = [p for p in points if p.rank == ranks[0]]
    points = sorted(points, key=lambda p: p.when)
    priced = [p for p in points if p.avg_price is not None]
    volume = sum(p.volume for p in points)
    avg = (sum(p.avg_price * p.volume for p in priced) / sum(p.volume for p in priced)
           if priced and sum(p.volume for p in priced) else None)
    medians = [p.median for p in points if p.median is not None]
    mins = [p.min_price for p in points if p.min_price is not None]
    maxs = [p.max_price for p in points if p.max_price is not None]

    change = None
    if len(priced) >= 2 * window:
        recent = [p.avg_price for p in priced[-window:]]
        before = [p.avg_price for p in priced[-2 * window:-window]]
        b = sum(before) / len(before)
        if b:
            change = round((sum(recent) / len(recent) - b) / b * 100, 1)
    elif len(priced) >= 2 and priced[0].avg_price:
        change = round((priced[-1].avg_price - priced[0].avg_price) / priced[0].avg_price * 100, 1)

    series = [p.avg_price for p in points]
    if len(series) > spark_width:                 # downsample by averaging buckets
        step = len(series) / spark_width
        buckets = []
        for i in range(spark_width):
            chunk = [v for v in series[int(i * step):int((i + 1) * step)] if v is not None]
            buckets.append(sum(chunk) / len(chunk) if chunk else None)
        series = buckets

    return HistorySummary(
        period=period, points=len(points), volume=volume,
        avg_price=round(avg, 1) if avg is not None else None,
        median_price=median(medians) if medians else None,
        min_price=min(mins) if mins else None, max_price=max(maxs) if maxs else None,
        first_price=priced[0].avg_price if priced else None,
        last_price=priced[-1].avg_price if priced else None,
        change_pct=change, sparkline=sparkline(series), ranks=ranks,
    )


# --------------------------------------------------------------------------- #
# Ducats
# --------------------------------------------------------------------------- #
@dataclass
class DucatDeal:
    item: Item
    ducats: int
    platinum: int                     # cheapest recent online sell order
    seller: str

    @property
    def ducats_per_plat(self) -> float:
        return self.ducats / self.platinum if self.platinum else 0.0


def ducat_deals(recent: Iterable[Order], items_by_id: Mapping[str, Item], *,
                min_ducats: int = 1, limit: int = 30) -> list[DucatDeal]:
    """Cheapest online sell order per ducat-able item among recent orders,
    best ducats-per-plat first. Buy these, trade them to Baro for ducats."""
    best: dict[str, DucatDeal] = {}
    for o in recent:
        if o.type != "sell" or not o.is_online:
            continue
        item = items_by_id.get(o.item_id)
        if item is None or not item.ducats or item.ducats < min_ducats or item.is_set:
            continue
        cur = best.get(item.id)
        if cur is None or o.platinum < cur.platinum:
            best[item.id] = DucatDeal(item, item.ducats, o.platinum, o.seller)
    deals = sorted(best.values(), key=lambda d: (-d.ducats_per_plat, d.platinum))
    return deals[:limit]


# --------------------------------------------------------------------------- #
# Recent trends
# --------------------------------------------------------------------------- #
@dataclass
class TrendRow:
    item: Item
    orders: int
    sells: int
    buys: int
    quantity: int
    avg_sell: Optional[int]
    avg_buy: Optional[int]


def recent_trends(recent: Iterable[Order], items_by_id: Mapping[str, Item], *,
                  min_orders: int = 3, limit: int = 50) -> list[TrendRow]:
    """Group the newest site-wide orders by item; busiest first."""
    groups: dict[str, list[Order]] = {}
    for o in recent:
        if o.item_id in items_by_id:
            groups.setdefault(o.item_id, []).append(o)
    rows = []
    for item_id, orders in groups.items():
        if len(orders) < min_orders:
            continue
        sells = [o.platinum for o in orders if o.type == "sell"]
        buys = [o.platinum for o in orders if o.type == "buy"]
        rows.append(TrendRow(
            item=items_by_id[item_id], orders=len(orders), sells=len(sells), buys=len(buys),
            quantity=sum(o.quantity for o in orders),
            avg_sell=round(sum(sells) / len(sells)) if sells else None,
            avg_buy=round(sum(buys) / len(buys)) if buys else None))
    rows.sort(key=lambda r: (-r.orders, r.item.name))
    return rows[:limit]


# --------------------------------------------------------------------------- #
# Lich / Sister weapon contracts
# --------------------------------------------------------------------------- #
ELEMENTS = ("impact", "heat", "cold", "electricity", "toxin", "magnetic", "radiation")


@dataclass(frozen=True)
class WeaponFilter:
    element: Optional[str] = None
    min_damage: Optional[int] = None
    ephemera: Optional[bool] = None
    max_price: Optional[int] = None
    online_only: bool = True


def filter_weapon_auctions(auctions: Iterable[WeaponAuction], flt: WeaponFilter) -> list[WeaponAuction]:
    kept = []
    for a in auctions:
        if flt.online_only and not a.is_online:
            continue
        if flt.element and a.element != flt.element.lower():
            continue
        if flt.min_damage is not None and a.damage < flt.min_damage:
            continue
        if flt.ephemera is not None and a.ephemera != flt.ephemera:
            continue
        if flt.max_price is not None:
            price = asking_price(a)
            if price is None or price > flt.max_price:
                continue
        kept.append(a)
    kept.sort(key=lambda a: (asking_price(a) is None, asking_price(a) or 0, -a.damage))
    return kept
