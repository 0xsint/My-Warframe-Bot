"""Discord embeds for the seller dashboard. set_image() puts the item picture
below the data (same as the wiki ability view)."""
from __future__ import annotations

from typing import Optional, Sequence

import nextcord

from .analysis import SellerReport, assess_price, top_orders, whisper
from .client import Item, Order, RivenAuction
from .riven import asking_price, stat_label

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


# =========================================================================== #
# Paginated / sectioned views (used by cogs/market_commands.py)
# =========================================================================== #
from statistics import median as _median          # noqa: E402

from ui.paginator import Section                   # noqa: E402
from ui.text import chunk                          # noqa: E402

GREEN, RED, GREY = 0x2ECC71, 0xE74C3C, 0x95A5A6
MARKET_FOOTER = "data from warframe.market"
STATUS_ICON = {"ingame": "\U0001F7E2 In game", "online": "\U0001F7E1 Online"}
MEDALS = {1: "\U0001F947", 2: "\U0001F948", 3: "\U0001F949"}


def _embed(title: str, *, url: Optional[str] = None, color: int = GOLD, description: str = "",
           thumb: Optional[str] = None, image: Optional[str] = None) -> nextcord.Embed:
    e = nextcord.Embed(title=title, url=url, color=color, description=description or None)
    if image:
        e.set_image(url=image)
    elif thumb:
        e.set_thumbnail(url=thumb)
    return e


def _bar(n: int, biggest: int, width: int = 8) -> str:
    return "\u2588" * max(1, round(width * n / biggest)) if biggest else ""


# --------------------------------------------------------------- /wm price ---
def report_sections(report: SellerReport, item: Item, *, platform: str,
                    my_price: Optional[int] = None) -> list[Section]:
    """The seller report split into dropdown sections (one embed each)."""
    rank_note = f" · rank {report.rank_filter}" if report.rank_filter is not None else ""
    head = f"*{report.market_tone}*\nPlatform: **{platform.upper()}**{rank_note}"
    name = report.item_name

    # ---- Overview: what to list at + what you can do right now
    ov = _embed(f"{name}: seller view", url=item.url, description=head, image=item.image_url)
    ov.add_field(name="\U0001F4B0 Suggested listing price",
                 value=(f"**Fast sale:** {_p(report.fast_price)}\n"
                        f"**Balanced:** {_p(report.balanced_price)}\n"
                        f"**Patient:** {_p(report.patient_price)}"), inline=True)
    ov.add_field(name="\u26A1 Sell right now",
                 value=(f"Best online buy order: **{_p(report.quick_sell_price)}**\n"
                        f"Online buyers: **{report.online_buyers}**\n"
                        f"Buyers at/above fast price: **{report.buyers_at_or_above_fast}**"), inline=True)
    if my_price is not None:
        a = assess_price(report, my_price)
        vs = "" if a["pct_vs_median"] is None else f" ({a['pct_vs_median']:+.0f}% vs median)"
        ov.add_field(name=f"\U0001F3AF Your price: {my_price}{PLAT}{vs}",
                     value=(f"{a['verdict']}\nCheaper online sellers: **{a['sellers_cheaper']}**"
                            f" (+{a['sellers_tied']} tied)"), inline=False)
    sections = [Section("Overview", [ov], description="Prices to list at and sell now", emoji="\U0001F4CA")]

    # ---- Competition
    comp = _embed(f"{name}: competition", url=item.url, description=head, thumb=item.image_url)
    comp.add_field(
        name="Online sellers",
        value=(f"Sellers: **{report.online_sellers}** (of {report.total_sell_orders} listed)\n"
               f"Lowest: **{_p(report.lowest_sell)}** · Median: **{_p(report.median_sell)}**\n"
               f"25th-75th pct: {_p(report.p25_sell)}-{_p(report.p75_sell)}\n"
               f"Spread to best buyer: {_p(report.spread)}"), inline=False)
    if report.price_ladder:
        biggest = max(l.orders for l in report.price_ladder)
        comp.add_field(name="Cheapest online listings", inline=False, value="\n".join(
            f"`{l.price:>5}{PLAT}` {_bar(l.orders, biggest)} {l.orders} seller{'s' if l.orders != 1 else ''}"
            for l in report.price_ladder))
    sections.append(Section("Competition", [comp], description="Who you're up against", emoji="\u2694\uFE0F"))

    # ---- Ranks
    if report.rank_table and report.mixed_ranks:
        rows = "\n".join(
            f"`R{row.rank:<2}` low **{_p(row.lowest_sell)}** · {row.online_sellers} sellers · "
            f"best buy **{_p(row.highest_buy)}**" for row in report.rank_table)
        sections.append(Section("By mod rank", [_embed(f"{name}: by mod rank", url=item.url,
                                                       description=rows, thumb=item.image_url)],
                                description="Compare prices per rank", emoji="\U0001F4F6"))

    # ---- Recent sales
    if report.history and report.history.get("volume_48h") is not None:
        h = report.history
        sections.append(Section("Last 48h sales", [_embed(
            f"{name}: last 48h sales", url=item.url, thumb=item.image_url,
            description=(f"Sold: **{h['volume_48h']}**\n"
                         f"Average: **{_p(h.get('avg_price_48h'))}** · Median: **{_p(h.get('median_48h'))}**"))],
            description="What it actually sold for", emoji="\U0001F4C8"))

    # ---- Tips
    if report.tips:
        sections.append(Section("Tips", [_embed(
            f"{name}: tips", url=item.url, thumb=item.image_url,
            description="\n".join(f"\u2022 {t}" for t in report.tips))],
            description=f"{len(report.tips)} suggestion{'s' if len(report.tips) != 1 else ''}",
            emoji="\U0001F4A1"))
    return sections


# ------------------------------------------------------ /wm buyers | sellers ---
def order_pages(item: Item, orders: Sequence[Order], *, side: str, rank: Optional[int],
                per_page: int = 6, limit: int = 48) -> list[nextcord.Embed]:
    """Online orders, `per_page` at a time, each page with its own whisper block.
    side='buy' -> buyers to whisper; side='sell' -> competing sellers."""
    picks = top_orders(orders, side=side, rank=rank, limit=limit)
    title = f"{item.name}: " + ("Buyers to whisper" if side == "buy" else "Cheapest sellers")
    if not picks:
        return [_embed(title, url=item.url, description="No online orders right now.", thumb=item.image_url)]

    prices = [o.platinum for o in picks]
    who = "buyers" if side == "buy" else "sellers"
    best = max(prices) if side == "buy" else min(prices)
    rank_note = f" · rank {rank}" if rank is not None else ""
    summary = (f"**{len(picks)}** online {who}{rank_note} · best **{best}{PLAT}** · "
               f"median **{_p(_median(prices))}**")

    pages = []
    for n, group in enumerate(chunk(picks, per_page)):
        lines = []
        for i, o in enumerate(group, start=n * per_page + 1):
            lines.append(f"`{i:>2}.` **{o.platinum}{PLAT}** \u00D7{o.quantity} \u00B7 {o.seller} \u00B7 "
                         f"{STATUS_ICON.get(o.status, o.status)}"
                         + (f" \u00B7 R{o.rank}" if o.rank is not None else ""))
        whispers = "\n".join(whisper(o, item.name) for o in group)
        desc = f"{summary}\n\n" + "\n".join(lines) + f"\n\n**Copy-paste whispers**\n```\n{whispers}\n```"
        pages.append(_embed(title, url=item.url, description=desc, thumb=item.image_url))
    return pages


# ------------------------------------------------------------- /wm riven ---
def _riven_block(i: int, a: RivenAuction, weapon_name: str | None = None) -> str:
    good, bad = "\u2705", "\u274C"
    stats = "\n".join(
        f"{good} {stat_label(s.url_name)} {s.value:+.1f}%" if s.positive
        else f"{bad} {stat_label(s.url_name)} {s.value:+.1f}% **(negative)**"
        for s in a.attributes)
    price = asking_price(a)
    if a.buyout_price:
        cost = f"Buyout **{a.buyout_price}{PLAT}**"
    elif price:
        cost = f"Bid **{price}{PLAT}**"
    else:
        cost = "Price **n/a**"
    pol = a.polarity.title() if a.polarity else "Unknown polarity"
    name = a.name or "Riven"
    weapon = f" \u00B7 {weapon_name}" if weapon_name else ""
    head = f"`{i:>2}.` **{name}**{weapon} \u00B7 R{a.mod_rank} \u00B7 {pol} \u00B7 {a.re_rolls} rerolls"
    return f"{head}\n{stats}\n{cost} \u00B7 {a.seller} \u00B7 {STATUS_ICON.get(a.status, a.status)}"


def riven_pages(item: Item, auctions: Sequence[RivenAuction], *, label: str | None = None,
                weapon_names: dict[str, str] | None = None, per_page: int = 4) -> list[nextcord.Embed]:
    """Riven contracts, `per_page` per embed. Input order is kept (already sorted)."""
    title = f"{label or item.name}: riven contracts"
    if not auctions:
        return [_embed(title, url=item.url, description="No riven contracts match your filters.",
                       thumb=item.image_url)]
    pages = []
    for n, group in enumerate(chunk(list(auctions), per_page)):
        blocks = [_riven_block(i, a, (weapon_names or {}).get(a.weapon))
                  for i, a in enumerate(group, start=n * per_page + 1)]
        summary = f"**{len(auctions)}** riven contract{'s' if len(auctions) != 1 else ''} \u00B7 cheapest first"
        pages.append(_embed(title, url=item.url, description=summary + "\n\n" + "\n\n".join(blocks),
                            thumb=item.image_url))
    return pages


# ------------------------------------------------------------- /wm trends ---
def trend_pages(trends: dict, *, per_page: int = 10) -> list[nextcord.Embed]:
    title = "Recent market trends"
    if not trends:
        return [_embed(title, description="Not enough recent orders to spot a trend.")]
    intro = "*Busiest items in the latest orders (items with 3+ orders), most active first.*"
    rows = list(trends.items())
    pages = []
    for n, group in enumerate(chunk(rows, per_page)):
        lines = []
        for i, (name, st) in enumerate(group, start=n * per_page + 1):
            badge = MEDALS.get(i, f"`{i:>2}.`")
            lines.append(f"{badge} **{name}**\n\u2003{st['count']} orders \u00B7 "
                         f"{st['total_quantity']} units \u00B7 avg **{st['average_platinum']}{PLAT}**")
        pages.append(_embed(title, description=intro + "\n\n" + "\n".join(lines)))
    return pages


# ----------------------------------------------------------- /wm my_orders ---
def _order_bits(order: dict) -> str:
    bits = [f"**{order.get('platinum')}{PLAT}**", f"qty {order.get('quantity')}"]
    for key, label in (("perTrade", "per trade"), ("rank", "rank"), ("charges", "charges"),
                       ("amberStars", "amber"), ("cyanStars", "cyan")):
        if order.get(key) is not None:
            bits.append(f"{label} {order[key]}")
    return " \u00B7 ".join(bits)


def my_orders_sections(orders: list[dict], *, per_page: int = 5) -> list[Section]:
    """`orders` is MarketSeller.get_my_orders(): [{'item': name, 'order_data': {...}}]."""
    def pages_for(subset: list[dict], title: str) -> list[nextcord.Embed]:
        sells = sum(1 for o in subset if o["order_data"].get("type") == "sell")
        head = f"**{len(subset)}** orders \u00B7 selling **{sells}** \u00B7 buying **{len(subset) - sells}**"
        out = []
        for n, group in enumerate(chunk(subset, per_page)):
            blocks = []
            for i, o in enumerate(group, start=n * per_page + 1):
                d = o["order_data"]
                kind = "\U0001F534 SELL" if d.get("type") == "sell" else "\U0001F7E2 BUY"
                shown = "\U0001F441 visible" if d.get("visible", True) else "\U0001F648 hidden"
                blocks.append(f"**{i}. {o['item']}**\n{kind} \u00B7 {shown}\n> {_order_bits(d)}\n"
                              f"> ID `{d.get('id')}`")
            out.append(_embed(title, description=head + "\n\n" + "\n\n".join(blocks)))
        return out

    if not orders:
        return [Section("All orders", [_embed("Your orders", description="You have no orders.")])]
    sells = [o for o in orders if o["order_data"].get("type") == "sell"]
    buys = [o for o in orders if o["order_data"].get("type") != "sell"]
    sections = [Section(f"All orders ({len(orders)})", pages_for(orders, "Your orders"))]
    if sells and buys:
        sections.append(Section(f"Selling ({len(sells)})", pages_for(sells, "Your sell orders"),
                                emoji="\U0001F534"))
        sections.append(Section(f"Buying ({len(buys)})", pages_for(buys, "Your buy orders"),
                                emoji="\U0001F7E2"))
    return sections


# ------------------------------------------------------ /wm ducats_compare ---
DUCATS_PER_PLAT_GOOD = 10      # common rule of thumb: ~10+ ducats per plat favours ducating


def ducats_embed(data: dict, item: Optional[Item] = None) -> nextcord.Embed:
    ducats, avg = int(data.get("ducats") or 0), float(data.get("average_platinum") or 0)
    e = _embed(f"{data['name']}: ducats vs platinum", url=item.url if item else None,
               thumb=item.image_url if item else None)
    e.add_field(name="Ducat value", value=f"**{ducats}**", inline=True)
    e.add_field(name="Avg online price", value=f"**{int(avg)}{PLAT}**" if avg else "n/a", inline=True)
    if ducats and avg:
        ratio = ducats / avg
        e.add_field(name="Ducats per plat", value=f"**{ratio:.1f}**", inline=True)
        if ratio >= DUCATS_PER_PLAT_GOOD:
            verdict = f"Ducats look like the better deal (about {DUCATS_PER_PLAT_GOOD}+ ducats per plat)."
        else:
            verdict = "Platinum looks like the better deal."
        e.add_field(name="Verdict", value=verdict + "\n*Rule of thumb only.*", inline=False)
    elif not ducats:
        e.add_field(name="Verdict", value="This item can't be sold for ducats.", inline=False)
    else:
        e.add_field(name="Verdict", value="No online orders to compare against.", inline=False)
    e.set_footer(text=MARKET_FOOTER)
    return e


# --------------------------------------------- /wm list | update | unlist ---
def _listing_fields(o, changed: frozenset = frozenset()) -> list[tuple[str, str]]:
    def mark(key, text):
        return f"\u270F\uFE0F {text}" if key in changed else text
    fields = [
        ("Type", mark("type", (o.order_type or "?").title())),
        ("Price", mark("platinum", f"**{o.platinum}{PLAT}**")),
        ("Quantity", mark("quantity", f"\u00D7{o.quantity}")),
    ]
    for key, label, val in (("rank", "Rank", o.rank), ("perTrade", "Per trade", o.perTrade),
                            ("charges", "Charges", o.mod_charges), ("subtype", "Subtype", o.subtype),
                            ("amberStars", "Amber stars", o.amberStars), ("cyanStars", "Cyan stars", o.cyanStars)):
        if val is not None:
            fields.append((label, mark(key, str(val))))
    fields.append(("Visibility", mark("visible", "Public" if o.visible else "Hidden (only you)")))
    return fields


def listed_embed(o) -> nextcord.Embed:
    e = _embed(f"Listed: {o.item_name}", color=GREEN)
    for name, value in _listing_fields(o):
        e.add_field(name=name, value=value, inline=True)
    e.set_footer(text=f"Order id: {o.id}  \u00B7  use /wm unlist to remove")
    return e


def updated_embed(o, changed: frozenset) -> nextcord.Embed:
    e = _embed(f"Updated: {o.item_name}", color=GOLD,
               description=("\u270F\uFE0F marks what you changed." if changed else "No fields were changed."))
    for name, value in _listing_fields(o, changed):
        e.add_field(name=name, value=value, inline=True)
    e.set_footer(text=f"Order id: {o.id}")
    return e


def removed_embed(order_id: str) -> nextcord.Embed:
    e = _embed("Listing removed", color=RED, description=f"Order `{order_id}` is no longer on warframe.market.")
    return e
