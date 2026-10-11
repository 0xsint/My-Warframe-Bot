"""Pure analysis helpers (no I/O): turn raw orders/stats into answers."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from .client import Item, Order

RANK_MODES = ("any", "unranked", "max")


# --------------------------------------------------------------------------- #
# Order filtering / price summary
# --------------------------------------------------------------------------- #
def rank_ok(order: Order, item: Item, mode: str) -> bool:
    """Rank filter. Only meaningful for rankable items (mods, arcanes)."""
    if mode == "any" or not item.max_rank:
        return True
    rank = order.rank or 0
    if mode == "unranked":
        return rank == 0
    if mode == "max":
        return rank == item.max_rank
    return True


def _status_rank(o: Order) -> int:
    return {"ingame": 0, "online": 1}.get(o.status, 2)


@dataclass(slots=True)
class PriceSummary:
    item: Item
    sells: list[Order]  # cheapest first
    buys: list[Order]  # highest first
    rank_mode: str = "any"
    online_only: bool = True

    @property
    def lowest_sell(self) -> Order | None:
        return self.sells[0] if self.sells else None

    @property
    def highest_buy(self) -> Order | None:
        return self.buys[0] if self.buys else None

    @property
    def spread(self) -> int | None:
        if self.lowest_sell and self.highest_buy:
            return self.lowest_sell.platinum - self.highest_buy.platinum
        return None

    @property
    def crossed(self) -> bool:
        """An online buyer bids >= an online seller's ask: instant-flip opportunity."""
        s = self.spread
        return s is not None and s <= 0

    @property
    def sell_quantity(self) -> int:
        return sum(o.quantity for o in self.sells)

    def median_ask(self, n: int = 5) -> float | None:
        prices = [o.platinum for o in self.sells[:n]]
        return statistics.median(prices) if prices else None


def build_summary(
    item: Item,
    orders: Iterable[Order],
    *,
    rank_mode: str = "any",
    online_only: bool = True,
) -> PriceSummary:
    pool = [o for o in orders if (o.online or not online_only) and rank_ok(o, item, rank_mode)]
    sells = sorted(
        (o for o in pool if o.type == "sell"),
        key=lambda o: (o.platinum, _status_rank(o), -o.reputation),
    )
    buys = sorted(
        (o for o in pool if o.type == "buy"),
        key=lambda o: (-o.platinum, _status_rank(o), -o.reputation),
    )
    return PriceSummary(item, sells, buys, rank_mode, online_only)


def rank_by_price(summaries: list[PriceSummary]) -> list[PriceSummary]:
    """Cheapest lowest-sell first; items with no sellers go last."""
    return sorted(
        summaries,
        key=lambda s: (s.lowest_sell is None, s.lowest_sell.platinum if s.lowest_sell else 0),
    )


# --------------------------------------------------------------------------- #
# Availability / purchase planning
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class PurchasePlan:
    wanted: int
    legs: list[tuple[Order, int]] = field(default_factory=list)  # (order, units taken)

    @property
    def units(self) -> int:
        return sum(n for _, n in self.legs)

    @property
    def total(self) -> int:
        return sum(o.platinum * n for o, n in self.legs)

    @property
    def shortfall(self) -> int:
        return max(self.wanted - self.units, 0)

    @property
    def complete(self) -> bool:
        return self.units >= self.wanted

    @property
    def average(self) -> float | None:
        return self.total / self.units if self.units else None


def plan_purchase(sells: list[Order], wanted: int = 1, max_price: int | None = None) -> PurchasePlan:
    """Cheapest way to buy ``wanted`` units from ``sells`` (sorted cheapest-first),
    optionally refusing any order priced above ``max_price``."""
    plan = PurchasePlan(wanted=wanted)
    need = wanted
    for o in sells:
        if max_price is not None and o.platinum > max_price:
            break
        take = min(need, o.quantity)
        if take <= 0:
            continue
        plan.legs.append((o, take))
        need -= take
        if need <= 0:
            break
    return plan


def whisper(order: Order, item: Item) -> str:
    """The in-game whisper warframe.market itself generates for a sell order."""
    label = item.name
    if item.max_rank and order.rank is not None:
        label += f" (rank {order.rank})"
    return f'/w {order.user} Hi! I want to buy: "{label}" for {order.platinum} platinum. (warframe.market)'


# --------------------------------------------------------------------------- #
# Set vs. parts
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class SetPart:
    item: Item
    quantity: int
    summary: PriceSummary


@dataclass(slots=True)
class SetBreakdown:
    root: SetPart
    parts: list[SetPart]

    @property
    def set_ask(self) -> int | None:
        s = self.root.summary.lowest_sell
        return s.platinum if s else None

    @property
    def set_bid(self) -> int | None:
        b = self.root.summary.highest_buy
        return b.platinum if b else None

    @property
    def parts_ask(self) -> int | None:
        """Cost of buying every part at its cheapest online ask."""
        total = 0
        for p in self.parts:
            s = p.summary.lowest_sell
            if s is None:
                return None
            total += s.platinum * p.quantity
        return total

    @property
    def parts_bid(self) -> int | None:
        """Revenue from selling every part into the best online bids."""
        total = 0
        for p in self.parts:
            b = p.summary.highest_buy
            if b is None:
                return None
            total += b.platinum * p.quantity
        return total

    @property
    def cheapest_way(self) -> tuple[str, int] | None:
        opts = []
        if self.set_ask is not None:
            opts.append(("Buy the full set", self.set_ask))
        if self.parts_ask is not None:
            opts.append(("Buy the parts separately", self.parts_ask))
        return min(opts, key=lambda t: t[1]) if opts else None

    @property
    def assemble_margin(self) -> int | None:
        """Buy all parts at ask, sell the set to the best bid."""
        if self.parts_ask is None or self.set_bid is None:
            return None
        return self.set_bid - self.parts_ask

    @property
    def break_margin(self) -> int | None:
        """Buy the set at ask, sell every part into the best bids."""
        if self.parts_bid is None or self.set_ask is None:
            return None
        return self.parts_bid - self.set_ask


# --------------------------------------------------------------------------- #
# Trends (closed-trade history)
# --------------------------------------------------------------------------- #
def _ts(row: dict[str, Any]) -> datetime | None:
    raw = row.get("datetime")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _stat_rank_ok(row: dict[str, Any], item: Item, mode: str) -> bool:
    if mode == "any" or not item.max_rank or "mod_rank" not in row:
        return True
    rank = row.get("mod_rank") or 0
    return rank == 0 if mode == "unranked" else rank == item.max_rank


def _vwap(rows: list[dict[str, Any]]) -> float | None:
    """Volume-weighted average price (falls back to a plain mean)."""
    if not rows:
        return None
    vol = sum(r.get("volume") or 0 for r in rows)
    if vol > 0:
        return sum((r.get("avg_price") or 0) * (r.get("volume") or 0) for r in rows) / vol
    prices = [r["avg_price"] for r in rows if r.get("avg_price") is not None]
    return sum(prices) / len(prices) if prices else None


@dataclass(slots=True)
class Trend:
    item: Item
    avg_7d: float | None
    avg_30d: float | None
    avg_prev_7d: float | None
    low_90d: float | None
    high_90d: float | None
    volume_48h: int
    volume_7d: int
    daily_prices: list[float]
    rank_mode: str

    @property
    def change_7d_pct(self) -> float | None:
        if self.avg_7d is None or not self.avg_prev_7d:
            return None
        return (self.avg_7d - self.avg_prev_7d) / self.avg_prev_7d * 100


def build_trend(item: Item, stats: dict[str, list[dict]], rank_mode: str = "any") -> Trend | None:
    daily = [r for r in stats.get("90days", []) if _stat_rank_ok(r, item, rank_mode)]
    hourly = [r for r in stats.get("48hours", []) if _stat_rank_ok(r, item, rank_mode)]
    dated = [(t, r) for r in daily if (t := _ts(r)) is not None]
    if not dated:
        return None
    dated.sort(key=lambda p: p[0])
    latest = dated[-1][0]

    def window(days_from: int, days_to: int) -> list[dict]:
        lo, hi = latest - timedelta(days=days_from), latest - timedelta(days=days_to)
        return [r for t, r in dated if lo < t <= hi]

    last7 = window(7, 0)
    return Trend(
        item=item,
        avg_7d=_vwap(last7),
        avg_30d=_vwap(window(30, 0)),
        avg_prev_7d=_vwap(window(14, 7)),
        low_90d=min((r["min_price"] for _, r in dated if r.get("min_price") is not None), default=None),
        high_90d=max((r["max_price"] for _, r in dated if r.get("max_price") is not None), default=None),
        volume_48h=sum(r.get("volume") or 0 for r in hourly),
        volume_7d=sum(r.get("volume") or 0 for r in last7),
        daily_prices=[r["avg_price"] for _, r in dated[-30:] if r.get("avg_price") is not None],
        rank_mode=rank_mode,
    )


_BLOCKS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float]) -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        return _BLOCKS[3] * len(values)
    step = (hi - lo) / (len(_BLOCKS) - 1)
    return "".join(_BLOCKS[round((v - lo) / step)] for v in values)
