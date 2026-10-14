"""Turn raw warframe.market orders into things a *seller* can act on.

Pure functions only (no network, no Discord) so the bot and the FastAPI app
share them and they are trivial to unit-test.

Vocabulary
  sell order = someone offering to sell        (competition for you)
  buy order  = someone offering to buy         (people you can sell to right now)
Only players who are "online"/"ingame" can actually trade, so headline numbers
use online orders only; offline orders are reported separately for context.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional, Sequence

from .client import Order


@dataclass
class PriceLevel:
    price: int
    orders: int


@dataclass
class RankRow:
    rank: int
    lowest_sell: Optional[int]
    online_sellers: int
    highest_buy: Optional[int]


@dataclass
class SellerReport:
    item_name: str
    slug: str
    rank_filter: Optional[int]

    online_sellers: int = 0
    total_sell_orders: int = 0
    online_buyers: int = 0
    total_buy_orders: int = 0

    lowest_sell: Optional[int] = None
    second_lowest_sell: Optional[int] = None
    p25_sell: Optional[float] = None
    median_sell: Optional[float] = None
    p75_sell: Optional[float] = None
    mean_sell: Optional[float] = None
    highest_buy: Optional[int] = None
    spread: Optional[int] = None
    offline_median_sell: Optional[float] = None

    fast_price: Optional[int] = None        # sells quickly (undercut the cheapest)
    balanced_price: Optional[int] = None    # solid middle ground
    patient_price: Optional[int] = None     # maximise platinum, wait longer
    quick_sell_price: Optional[int] = None  # take the best open buy order right now
    buyers_at_or_above_fast: int = 0

    market_tone: str = ""
    outlier_low: bool = False
    mixed_ranks: bool = False
    price_ladder: list[PriceLevel] = field(default_factory=list)
    online_sell_prices: list[int] = field(default_factory=list)   # sorted ascending
    rank_table: list[RankRow] = field(default_factory=list)
    history: Optional[dict] = None
    tips: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
def percentile(sorted_vals: Sequence[float], p: float) -> Optional[float]:
    """Linear-interpolated percentile (p in 0..100) of an already sorted list."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _round(x: Optional[float]) -> Optional[int]:
    return None if x is None else int(round(x))


def build_report(item_name: str, slug: str, orders: Sequence[Order], *,
                 rank: Optional[int] = None, history: Optional[dict] = None,
                 ladder_size: int = 6) -> SellerReport:
    r = SellerReport(item_name=item_name, slug=slug, rank_filter=rank, history=history)

    ranks_present = sorted({o.rank for o in orders if o.rank is not None})
    r.mixed_ranks = rank is None and len(ranks_present) > 1
    if ranks_present:
        r.rank_table = _rank_table(orders, ranks_present)

    if rank is not None:
        orders = [o for o in orders if o.rank == rank]

    sells = [o for o in orders if o.type == "sell"]
    buys = [o for o in orders if o.type == "buy"]
    on_sells = sorted(o.platinum for o in sells if o.is_online)
    on_buys = sorted((o.platinum for o in buys if o.is_online), reverse=True)
    off_sells = sorted(o.platinum for o in sells if not o.is_online)

    r.online_sellers, r.total_sell_orders = len(on_sells), len(sells)
    r.online_buyers, r.total_buy_orders = len(on_buys), len(buys)
    r.offline_median_sell = percentile(off_sells, 50)

    if on_sells:
        r.lowest_sell = on_sells[0]
        r.second_lowest_sell = on_sells[1] if len(on_sells) > 1 else None
        r.p25_sell = percentile(on_sells, 25)
        r.median_sell = percentile(on_sells, 50)
        r.p75_sell = percentile(on_sells, 75)
        r.mean_sell = sum(on_sells) / len(on_sells)
        r.price_ladder = _ladder(on_sells, ladder_size)
        r.online_sell_prices = on_sells
    if on_buys:
        r.highest_buy = on_buys[0]
    if r.lowest_sell is not None and r.highest_buy is not None:
        r.spread = r.lowest_sell - r.highest_buy

    # A single lowball listing (bait / mistake) shouldn't set your price.
    ref_low = r.lowest_sell
    if (len(on_sells) >= 3 and r.second_lowest_sell is not None
            and r.lowest_sell < 0.7 * r.second_lowest_sell
            and r.second_lowest_sell - r.lowest_sell >= 5):
        r.outlier_low = True
        ref_low = r.second_lowest_sell

    if ref_low is not None:
        fast = max(ref_low - 1, 1)
        if r.highest_buy is not None:
            fast = max(fast, r.highest_buy)       # never list below an open buy order
        balanced = max(_round(r.p25_sell) or fast, fast)
        patient = max(_round(r.median_sell) or balanced, balanced)
        r.fast_price, r.balanced_price, r.patient_price = fast, balanced, patient
        r.buyers_at_or_above_fast = sum(1 for b in on_buys if b >= fast)
    r.quick_sell_price = r.highest_buy

    r.market_tone = _tone(r.online_sellers, r.online_buyers)
    r.tips = _tips(r)
    return r


def _ladder(sorted_prices: Sequence[int], size: int) -> list[PriceLevel]:
    levels: list[PriceLevel] = []
    for p in sorted_prices:
        if levels and levels[-1].price == p:
            levels[-1].orders += 1
        elif len(levels) < size:
            levels.append(PriceLevel(p, 1))
        else:
            break
    return levels


def _rank_table(orders: Sequence[Order], ranks: Sequence[int]) -> list[RankRow]:
    rows = []
    for rk in ranks:
        s = sorted(o.platinum for o in orders if o.rank == rk and o.type == "sell" and o.is_online)
        b = sorted((o.platinum for o in orders if o.rank == rk and o.type == "buy" and o.is_online),
                   reverse=True)
        rows.append(RankRow(rk, s[0] if s else None, len(s), b[0] if b else None))
    return rows


def _tone(sellers: int, buyers: int) -> str:
    if sellers == 0 and buyers == 0:
        return "Dead market: nobody online is trading this right now"
    if sellers == 0:
        return "No online competition: buyers are waiting"
    ratio = buyers / sellers
    if ratio >= 1.0:
        return "Seller's market: demand is at or above supply"
    if ratio >= 0.4:
        return "Balanced market"
    return "Buyer's market: lots of sellers per buyer, expect to compete on price"


def _tips(r: SellerReport) -> list[str]:
    tips: list[str] = []
    if r.mixed_ranks:
        tips.append("Prices mix several mod ranks. Re-run with a `rank` to compare like with like.")
    if r.online_sellers == 0:
        if r.offline_median_sell:
            tips.append(f"No online sellers. Offline listings sit around {_round(r.offline_median_sell)}p, "
                        "so you can likely list near that.")
        else:
            tips.append("No sell orders at all. Check the item's recent sales before pricing.")
        return tips
    if r.outlier_low:
        tips.append(f"The cheapest listing ({r.lowest_sell}p) is far below the rest. It's probably a "
                    "mistake or bait. Price against the next one instead.")
    if r.highest_buy is not None and r.lowest_sell is not None and r.highest_buy >= r.lowest_sell:
        tips.append(f"An online buyer offers {r.highest_buy}p, at or above the cheapest seller. "
                    "That's an instant sale, so whisper them first.")
    if r.spread is not None and r.lowest_sell and r.spread > 0.25 * r.lowest_sell:
        tips.append(f"Wide spread ({r.spread}p). Buyers aren't meeting current asks, so a price near the "
                    "middle will attract them.")
    if r.online_sellers >= 8:
        tips.append("Crowded: many sellers are online. Undercut by 1p or sell in a bundle/set to stand out.")
    if r.online_sellers <= 2 and r.online_buyers >= 3:
        tips.append("Low supply and active buyers. Don't rush to undercut; the patient price is realistic.")
    if r.history and r.history.get("median_48h") and r.median_sell:
        med48 = r.history["median_48h"]
        if med48 and r.median_sell > 1.25 * med48:
            tips.append(f"Current asks ({_round(r.median_sell)}p median) are well above recent sales "
                        f"({_round(med48)}p). Sellers are overshooting; expect to cut price.")
        elif med48 and r.median_sell < 0.8 * med48:
            tips.append(f"Current asks ({_round(r.median_sell)}p median) are below recent sales "
                        f"({_round(med48)}p). Prices may be recovering, so don't dump cheap.")
    return tips


# --------------------------------------------------------------------------- #
def assess_price(report: SellerReport, price: int) -> dict:
    """Where would a listing at `price` stand among current online sellers?"""
    prices = report.online_sell_prices
    cheaper = sum(1 for p in prices if p < price)
    tied = sum(1 for p in prices if p == price)
    med = report.median_sell
    out = {
        "price": price,
        "sellers_cheaper": cheaper,
        "sellers_tied": tied,
        "online_sellers": len(prices),
        "pct_vs_median": round((price - med) / med * 100, 1) if med else None,
    }
    if report.fast_price is None:
        out["verdict"] = "No online sellers to compare against."
    elif price <= report.fast_price:
        out["verdict"] = "Priced to sell fast."
    elif price <= (report.balanced_price or price):
        out["verdict"] = "Competitive. Should sell soon."
    elif price <= (report.patient_price or price):
        out["verdict"] = "Patient pricing. Fine if you can wait."
    else:
        out["verdict"] = "Above the typical online ask. Expect it to sit unless supply drops."
    return out


# --------------------------------------------------------------------------- #
def whisper(order: Order, item_name: str) -> str:
    """The exact in-game whisper warframe.market's own site generates."""
    verb = "buy" if order.type == "sell" else "sell"
    rank = f" (rank {order.rank})" if order.rank is not None else ""
    return (f'/w {order.seller} Hi! I want to {verb}: "{item_name}"{rank} '
            f"for {order.platinum} platinum. (warframe.market)")


def top_orders(orders: Sequence[Order], *, side: str, online_only: bool = True,
               rank: Optional[int] = None, limit: int = 5) -> list[Order]:
    """Best orders for a seller to act on.

    side="buy"  -> best open buy orders (highest price first): people you can sell to.
    side="sell" -> cheapest sell orders (lowest first): your competition.
    """
    pool = [o for o in orders if o.type == side]
    if online_only:
        pool = [o for o in pool if o.is_online]
    if rank is not None:
        pool = [o for o in pool if o.rank == rank]
    pool.sort(key=lambda o: (-o.platinum if side == "buy" else o.platinum, -o.reputation))
    return pool[:limit]
