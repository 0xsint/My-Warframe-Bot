"""Discord embeds for the seller dashboard. set_image() puts the item picture
below the data (same as the wiki ability view)."""
from __future__ import annotations

from typing import Optional, Sequence

import nextcord

from .analysis import SellerReport, assess_price, top_orders, whisper
from .client import Item, Order

GOLD = 0xE6B422
PLAT = "p"   # platinum suffix; swap for a custom emoji if your bot has one


def _p(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{int(round(value))}{PLAT}"


def report_embed(report: SellerReport, item: Item, *, platform: str,
                 my_price: Optional[int] = None) -> nextcord.Embed:
    e = nextcord.Embed(title=f"{report.item_name}: seller view", url=item.url, color=GOLD)
    rank_note = f" · rank {report.rank_filter}" if report.rank_filter is not None else ""
    e.description = f"*{report.market_tone}*\nPlatform: **{platform.upper()}**{rank_note}"

    e.add_field(
        name="Suggested listing price",
        value=(f"**Fast sale:** {_p(report.fast_price)}\n"
               f"**Balanced:** {_p(report.balanced_price)}\n"
               f"**Patient:** {_p(report.patient_price)}"),
        inline=True,
    )
    quick = _p(report.quick_sell_price)
    e.add_field(
        name="Sell right now",
        value=(f"Best online buy order: **{quick}**\n"
               f"Online buyers: **{report.online_buyers}**\n"
               f"Buyers at/above fast price: **{report.buyers_at_or_above_fast}**"),
        inline=True,
    )
    e.add_field(
        name="Competition (online)",
        value=(f"Sellers: **{report.online_sellers}** (of {report.total_sell_orders} listed)\n"
               f"Lowest: **{_p(report.lowest_sell)}** · Median: **{_p(report.median_sell)}**\n"
               f"25th-75th pct: {_p(report.p25_sell)}-{_p(report.p75_sell)}\n"
               f"Spread to best buyer: {_p(report.spread)}"),
        inline=False,
    )

    if report.price_ladder:
        ladder = "\n".join(
            f"`{lvl.price:>5}{PLAT}` x {lvl.orders} seller{'s' if lvl.orders != 1 else ''}"
            for lvl in report.price_ladder)
        e.add_field(name="Cheapest online listings", value=ladder, inline=True)

    if report.rank_table and report.mixed_ranks:
        rows = "\n".join(
            f"`R{row.rank:<2}` low {_p(row.lowest_sell)} · {row.online_sellers} sellers · "
            f"buy {_p(row.highest_buy)}" for row in report.rank_table[:8])
        e.add_field(name="By mod rank", value=rows, inline=True)

    if report.history and report.history.get("volume_48h") is not None:
        h = report.history
        e.add_field(
            name="Last 48h sales",
            value=(f"Sold: **{h['volume_48h']}**\n"
                   f"Avg: **{_p(h.get('avg_price_48h'))}** · Median: **{_p(h.get('median_48h'))}**"),
            inline=True)

    if my_price is not None:
        a = assess_price(report, my_price)
        vs = "" if a["pct_vs_median"] is None else f" ({a['pct_vs_median']:+.0f}% vs median)"
        e.add_field(
            name=f"Your price: {my_price}{PLAT}{vs}",
            value=(f"{a['verdict']}\nCheaper online sellers: **{a['sellers_cheaper']}**"
                   f" (+{a['sellers_tied']} tied)"),
            inline=False)

    if report.tips:
        e.add_field(name="Tips", value="\n".join(f"- {t}" for t in report.tips)[:1024], inline=False)

    if item.image_url:
        e.set_image(url=item.image_url)                  # <- picture below the data
    e.set_footer(text="Online/in-game players only · data from warframe.market")
    return e


def whisper_embed(item: Item, orders: Sequence[Order], *, side: str, rank: Optional[int],
                  limit: int = 8) -> nextcord.Embed:
    """side='buy'  -> buyers you can whisper to sell.
       side='sell' -> competing sellers (for checking undercuts)."""
    picks = top_orders(orders, side=side, rank=rank, limit=limit)
    title = "Buyers to whisper" if side == "buy" else "Cheapest sellers"
    e = nextcord.Embed(title=f"{item.name}: {title}", url=item.url, color=GOLD)
    if not picks:
        e.description = "No online orders right now."
    else:
        lines = [f"**{o.platinum}{PLAT}** x{o.quantity} · {o.seller} · "
                 f"{'in game' if o.status == 'ingame' else 'online'}"
                 + (f" · R{o.rank}" if o.rank is not None else "")
                 for o in picks]
        e.description = "\n".join(lines)
        block = "\n".join(whisper(o, item.name) for o in picks)
        if len(block) > 950:
            block = "\n".join(whisper(o, item.name) for o in picks[:4])
        e.add_field(name="Copy-paste whispers (in-game chat)", value=f"```\n{block}\n```", inline=False)
    if item.image_url:
        e.set_image(url=item.image_url)
    e.set_footer(text="Whispers use warframe.market's standard format")
    return e
