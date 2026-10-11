"""Turn analysis results into Discord embeds."""

from __future__ import annotations

import nextcord
from nextcord.utils import escape_markdown

from .analysis import (
    PriceSummary,
    PurchasePlan,
    SetBreakdown,
    Trend,
    rank_by_price,
    sparkline,
    whisper,
)
from .client import Item, Order

GREEN = 0x2ECC71
GREY = 0x95A5A6
RED = 0xE74C3C
BLUE = 0x3498DB
GOLD = 0xF1C40F

_STATUS = {"ingame": "🟢", "online": "🟡"}
FOOTER = "Data: warframe.market · live orders, cached up to 60s"


def _clip(text: str, limit: int = 1024) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _p(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"{value:g}p" if isinstance(value, float) else f"{value}p"


def _rank_note(item: Item, mode: str) -> str:
    if not item.max_rank:
        return ""
    return {
        "any": " · all ranks",
        "unranked": " · unranked",
        "max": f" · max rank ({item.max_rank})",
    }[mode]


def _order_line(o: Order, item: Item) -> str:
    rank = f" R{o.rank}" if item.max_rank and o.rank is not None else ""
    qty = f" ×{o.quantity}" if o.quantity > 1 else ""
    name = escape_markdown(o.user)
    return f"{_STATUS.get(o.status, '⚫')} `{o.platinum:>5}p`{qty}{rank} · {name}"


def _base(title: str, item: Item | None, color: int) -> nextcord.Embed:
    e = nextcord.Embed(title=title, url=item.url if item else None, color=color)
    e.set_footer(text=FOOTER)
    return e


# --------------------------------------------------------------------------- #
def price_embed(s: PriceSummary, platform: str, trend: Trend | None = None) -> nextcord.Embed:
    item = s.item
    e = _base(f"{item.name} — price check", item, GREEN if s.sells else GREY)
    scope = "online" if s.online_only else "all"
    e.description = (
        f"**Lowest sell:** {_p(s.lowest_sell.platinum if s.lowest_sell else None)}  ·  "
        f"**Highest buy:** {_p(s.highest_buy.platinum if s.highest_buy else None)}  ·  "
        f"**Spread:** {_p(s.spread)}\n"
        f"-# {platform.upper()} · {scope} orders{_rank_note(item, s.rank_mode)}"
    )
    if s.crossed:
        e.add_field(
            name="⚡ Instant flip",
            value="An online buyer is bidding at or above the cheapest ask.",
            inline=False,
        )
    e.add_field(
        name=f"Cheapest sellers ({len(s.sells)} listed)",
        value=_clip("\n".join(_order_line(o, item) for o in s.sells[:5]) or "No sellers online."),
        inline=False,
    )
    e.add_field(
        name=f"Top buyers ({len(s.buys)} listed)",
        value=_clip("\n".join(_order_line(o, item) for o in s.buys[:5]) or "No buyers online."),
        inline=False,
    )
    extras = []
    if s.sells:
        extras.append(f"Median of 5 cheapest asks: **{_p(s.median_ask())}**")
        extras.append(f"Units for sale: **{s.sell_quantity}**")
    if item.ducats:
        extras.append(f"Ducats: **{item.ducats}**")
    if item.vaulted:
        extras.append("Vaulted")
    if trend:
        chg = trend.change_7d_pct
        chg_s = f" ({chg:+.0f}% vs prior week)" if chg is not None else ""
        extras.append(f"7d avg sold: **{_p(round(trend.avg_7d) if trend.avg_7d else None)}**{chg_s}")
    if extras:
        e.add_field(name="Market", value=_clip("\n".join(extras)), inline=False)
    return e


def compare_embed(summaries: list[PriceSummary], platform: str) -> nextcord.Embed:
    ranked = rank_by_price(summaries)
    e = _base("Price comparison", None, BLUE)
    w = max(len(s.item.name[:24]) for s in ranked)
    header = f"{'Item'.ljust(w)}  {'Sell':>6}  {'Buy':>6}  {'Spread':>6}  {'Sellers':>7}"
    rows = [header, "─" * len(header)]
    for s in ranked:
        rows.append(
            f"{s.item.name[:24].ljust(w)}  "
            f"{_p(s.lowest_sell.platinum if s.lowest_sell else None):>6}  "
            f"{_p(s.highest_buy.platinum if s.highest_buy else None):>6}  "
            f"{_p(s.spread):>6}  {len(s.sells):>7}"
        )
    e.description = f"```\n{chr(10).join(rows)}\n```\n-# {platform.upper()} · online orders only"
    priced = [s for s in ranked if s.lowest_sell]
    if len(priced) >= 2:
        a, b = priced[0], priced[1]
        diff = b.lowest_sell.platinum - a.lowest_sell.platinum
        e.add_field(
            name="Cheapest to buy",
            value=f"**{a.item.name}** at {_p(a.lowest_sell.platinum)} "
            f"({_p(diff)} less than {b.item.name})",
            inline=False,
        )
    elif len(priced) == 1:
        e.add_field(name="Cheapest to buy", value=f"**{priced[0].item.name}** (only one with sellers)", inline=False)
    else:
        e.add_field(name="Nobody is selling", value="No online sellers for any of these.", inline=False)
    flips = [s.item.name for s in ranked if s.crossed]
    if flips:
        e.add_field(name="⚡ Instant flips", value=", ".join(flips), inline=False)
    return e


def availability_embed(s: PriceSummary, plan: PurchasePlan, max_price: int | None, platform: str) -> nextcord.Embed:
    item = s.item
    budget = f" at ≤ {max_price}p each" if max_price is not None else ""
    if plan.complete:
        title, color = f"✅ {item.name} is available", GREEN
    elif plan.units:
        title, color = f"⚠️ Only {plan.units}/{plan.wanted} of {item.name} available", GOLD
    else:
        title, color = f"❌ {item.name} is not available{budget}", RED
    e = _base(title, item, color)

    lines = [f"Wanted: **{plan.wanted}**{budget}"]
    if plan.units:
        lines.append(f"Cheapest way to get {plan.units}: **{_p(plan.total)}** total (avg {_p(round(plan.average, 1))})")
    if not plan.complete and s.lowest_sell:
        # Explain *why*: either nobody's selling, or only over budget.
        if max_price is not None and s.lowest_sell.platinum > max_price:
            lines.append(f"Cheapest online ask is {_p(s.lowest_sell.platinum)}, over your budget.")
        elif not plan.units:
            lines.append("No units match your filters.")
    if not s.sells:
        lines.append("Nobody is selling this online right now.")
    lines.append(f"-# {platform.upper()} · online sellers only{_rank_note(item, s.rank_mode)}")
    e.description = "\n".join(lines)

    if plan.legs:
        e.add_field(
            name="Sellers to contact",
            value=_clip(
                "\n".join(
                    f"{_order_line(o, item)}" + (f"  (take {n})" if n != o.quantity else "")
                    for o, n in plan.legs[:8]
                )
            ),
            inline=False,
        )
        msgs = "\n".join(whisper(o, item) for o, _ in plan.legs[:3])
        e.add_field(name="Copy-paste whispers", value=_clip(f"```\n{msgs}\n```"), inline=False)
    return e


def set_embed(b: SetBreakdown, platform: str) -> nextcord.Embed:
    e = _base(f"{b.root.item.name} — set vs parts", b.root.item, BLUE)
    lines = []
    for p in b.parts:
        s = p.summary
        qty = f"{p.quantity}× " if p.quantity > 1 else ""
        lines.append(f"{qty}{p.item.name}: ask **{_p(s.lowest_sell.platinum if s.lowest_sell else None)}**"
                     f" · bid {_p(s.highest_buy.platinum if s.highest_buy else None)}")
    e.add_field(name="Parts (cheapest ask · best bid)", value=_clip("\n".join(lines) or "—"), inline=False)
    e.add_field(
        name="Totals",
        value=(
            f"Set: ask **{_p(b.set_ask)}** · bid {_p(b.set_bid)}\n"
            f"Parts: ask **{_p(b.parts_ask)}** · bid {_p(b.parts_bid)}"
        ),
        inline=False,
    )
    verdict = []
    cw = b.cheapest_way
    if cw:
        verdict.append(f"**Cheapest way to own it:** {cw[0].lower()} ({_p(cw[1])})")
    if b.assemble_margin is not None:
        verdict.append(f"Buy parts → sell set to top bid: **{b.assemble_margin:+d}p**")
    if b.break_margin is not None:
        verdict.append(f"Buy set → sell parts to top bids: **{b.break_margin:+d}p**")
    if verdict:
        e.add_field(name="Verdict", value=_clip("\n".join(verdict)), inline=False)
    e.description = f"-# {platform.upper()} · online orders only · margins ignore the time cost of trading"
    return e


def trend_embed(t: Trend, platform: str) -> nextcord.Embed:
    item = t.item
    e = _base(f"{item.name} — price trend", item, BLUE)
    chg = t.change_7d_pct
    e.description = (
        f"`{sparkline(t.daily_prices)}`  *(last {len(t.daily_prices)} trading days)*\n"
        f"-# {platform.upper()} · closed trades{_rank_note(item, t.rank_mode)}"
    )
    e.add_field(name="7-day avg", value=_p(round(t.avg_7d) if t.avg_7d else None), inline=True)
    e.add_field(name="30-day avg", value=_p(round(t.avg_30d) if t.avg_30d else None), inline=True)
    e.add_field(name="Week over week", value=f"{chg:+.1f}%" if chg is not None else "—", inline=True)
    e.add_field(name="90-day low", value=_p(t.low_90d), inline=True)
    e.add_field(name="90-day high", value=_p(t.high_90d), inline=True)
    e.add_field(name="Volume", value=f"{t.volume_48h} (48h) · {t.volume_7d} (7d)", inline=True)
    return e
