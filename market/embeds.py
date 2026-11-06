"""Discord embeds for every /wm command.

set_image() puts the item picture *below* the data (same as the wiki views);
set_thumbnail() is used on secondary pages so the text stays readable.
Builders return plain embeds or ui.paginator.Section lists; the cog wraps them
in a SectionView.
"""
from __future__ import annotations

from statistics import median as _median
from typing import Optional, Sequence

import nextcord

from ui.paginator import Section
from ui.text import chunk

from .analysis import SellerReport, assess_price, top_orders, whisper
from .client import Item, ItemDetail, Order, RivenAuction, RivenWeapon, UserProfile, WeaponAuction
from .insights import DucatDeal, HistorySummary, SetReport, TrendRow
from .riven import asking_price, stat_label

GOLD, GREEN, RED, GREY, BLUE = 0xE6B422, 0x2ECC71, 0xE74C3C, 0x95A5A6, 0x3498DB
PLAT = "p"   # platinum suffix; swap for a custom emoji if your bot has one
MARKET_FOOTER = "data from warframe.market"
STATUS_ICON = {"ingame": "\U0001F7E2 In game", "online": "\U0001F7E1 Online",
               "offline": "\u26AB Offline", "invisible": "\u26AB Invisible"}
MEDALS = {1: "\U0001F947", 2: "\U0001F948", 3: "\U0001F949"}
ELEMENT_ICON = {"impact": "\U0001F528", "heat": "\U0001F525", "cold": "\u2744\uFE0F",
                "electricity": "\u26A1", "toxin": "\u2620\uFE0F", "magnetic": "\U0001F9F2",
                "radiation": "\u2622\uFE0F"}


def _p(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{int(round(value))}{PLAT}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


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
    cost = f"[{cost}]({a.url})" if a.id else cost
    pol = a.polarity.title() if a.polarity else "Unknown polarity"
    name = a.name or "Riven"
    weapon = f" \u00B7 {weapon_name}" if weapon_name else ""
    head = f"`{i:>2}.` **{name}**{weapon} \u00B7 R{a.mod_rank} \u00B7 {pol} \u00B7 {a.re_rolls} rerolls"
    return f"{head}\n{stats}\n{cost} \u00B7 {a.seller} \u00B7 {STATUS_ICON.get(a.status, a.status)}"


def riven_pages(item: Item, auctions: Sequence[RivenAuction], *, label: str | None = None,
                weapon_names: dict[str, str] | None = None, per_page: int = 4,
                info: RivenWeapon | None = None) -> list[nextcord.Embed]:
    """Riven contracts, `per_page` per embed. Input order is kept (already sorted).
    ``info`` adds the weapon's riven disposition to the summary line."""
    title = f"{label or item.name}: riven contracts"
    dispo = ""
    if info is not None and info.disposition is not None:
        dispo = f" \u00B7 disposition {info.disposition_dots} ({info.disposition:.2f})"
    if not auctions:
        return [_embed(title, url=item.url, description="No riven contracts match your filters.",
                       thumb=item.image_url)]
    pages = []
    for n, group in enumerate(chunk(list(auctions), per_page)):
        blocks = [_riven_block(i, a, (weapon_names or {}).get(a.weapon))
                  for i, a in enumerate(group, start=n * per_page + 1)]
        summary = f"**{len(auctions)}** riven contract{'s' if len(auctions) != 1 else ''} \u00B7 cheapest first{dispo}"
        pages.append(_embed(title, url=item.url, description=summary + "\n\n" + "\n\n".join(blocks),
                            thumb=item.image_url))
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


def closed_embed(o) -> nextcord.Embed:
    e = _embed(f"Marked as {'sold' if o.order_type == 'sell' else 'bought'}: {o.item_name}", color=GREEN,
               description="The trade now counts in warframe.market's statistics.")
    e.add_field(name="Price", value=f"**{o.platinum}{PLAT}**", inline=True)
    e.add_field(name="Left on the order", value=f"×{o.quantity}", inline=True)
    e.set_footer(text=f"Order id: {o.id}")
    return e


# ---------------------------------------------------------------- /wm item ---
def _flag(value: Optional[bool], yes: str, no: str) -> Optional[str]:
    return None if value is None else (yes if value else no)


def item_embed(detail: ItemDetail, top: Optional[dict[str, list[Order]]] = None) -> nextcord.Embed:
    """Item card: game facts from warframe.market plus the current best prices."""
    it = detail.item
    e = _embed(it.name, url=it.url, description=detail.description[:1500] or None,
               thumb=it.thumb_url, color=BLUE)
    facts = []
    if detail.rarity:
        facts.append(f"**Rarity:** {detail.rarity.title()}")
    if detail.mastery_rank:
        facts.append(f"**Mastery rank:** {detail.mastery_rank}")
    if it.max_rank is not None:
        facts.append(f"**Max rank:** {it.max_rank}")
    if it.max_charges is not None:
        facts.append(f"**Charges:** {it.max_charges}")
    if it.max_amber_stars or it.max_cyan_stars:
        facts.append(f"**Stars:** {it.max_amber_stars or 0} amber · {it.max_cyan_stars or 0} cyan")
    if it.subtypes:
        facts.append(f"**Subtypes:** {', '.join(s.title() for s in it.subtypes)}")
    vault = _flag(it.vaulted, "\U0001F512 Vaulted", "\U0001F513 Not vaulted")
    if vault and it.is_prime:
        facts.append(vault)
    if detail.quantity_in_set and detail.quantity_in_set > 1:
        facts.append(f"**Needed per set:** ×{detail.quantity_in_set}")
    if facts:
        e.add_field(name="Item", value="\n".join(facts)[:1024], inline=True)

    trade = []
    if detail.trading_tax is not None:
        trade.append(f"**Trading tax:** {detail.trading_tax:,} credits")
    if it.ducats:
        trade.append(f"**Ducats:** {it.ducats}")
    if detail.endo_at_max:
        trade.append(f"**Endo (max rank):** {detail.endo_at_max:,}")
    elif detail.base_endo:
        trade.append(f"**Endo (unranked):** {detail.base_endo:,}")
    if detail.vosfor:
        trade.append(f"**Vosfor:** {detail.vosfor}")
    if detail.bulk_tradable:
        trade.append("Bulk tradable")
    if trade:
        e.add_field(name="Trading", value="\n".join(trade), inline=True)

    if top is not None:
        sell, buy = top.get("sell") or [], top.get("buy") or []
        lines = [f"Cheapest online seller: **{_p(sell[0].platinum) if sell else 'n/a'}**",
                 f"Best online buyer: **{_p(buy[0].platinum) if buy else 'n/a'}**"]
        if it.ducats and sell:
            lines.append(f"Ducats per plat: **{it.ducats / sell[0].platinum:.1f}**")
        e.add_field(name="Market now", value="\n".join(lines), inline=False)

    links = [f"[warframe.market]({it.url})"]
    if detail.wiki_url:
        links.append(f"[WARFRAME Wiki]({detail.wiki_url})")
    e.add_field(name="Links", value=" · ".join(links), inline=False)
    if it.tags:
        e.set_footer(text="Tags: " + ", ".join(it.tags)[:200])
    return e


# ----------------------------------------------------------------- /wm top ---
def top_embed(item: Item, top: dict[str, list[Order]], *, rank: Optional[int] = None) -> nextcord.Embed:
    rank_note = f" (rank {rank})" if rank is not None else ""
    e = _embed(f"{item.name}{rank_note}: best orders", url=item.url, thumb=item.thumb_url)
    for side, label in (("sell", "\U0001F534 Cheapest sellers"), ("buy", "\U0001F7E2 Best buyers")):
        orders = top.get(side) or []
        if not orders:
            e.add_field(name=label, value="Nobody online.", inline=True)
            continue
        lines = [f"**{o.platinum}{PLAT}** ×{o.quantity} · {o.seller}"
                 + (f" · R{o.rank}" if o.rank is not None else "") for o in orders]
        e.add_field(name=label, value="\n".join(lines)[:1024], inline=True)
    picks = (top.get("sell") or [])[:1] + (top.get("buy") or [])[:1]
    if picks:
        block = "\n".join(whisper(o, item.name) for o in picks)
        e.add_field(name="Whispers (best seller / best buyer)", value=f"```\n{block[:990]}\n```", inline=False)
    return e


# ----------------------------------------------------------------- /wm set ---
def set_embed(report: SetReport, image: Optional[str] = None, url: Optional[str] = None) -> nextcord.Embed:
    e = _embed(f"{report.set_name}: set vs parts", url=url, thumb=image)
    lines = []
    for p in report.parts:
        qty = f" ×{p.quantity}" if p.quantity > 1 else ""
        ducats = f" · {p.ducats * p.quantity}\U0001FA99" if p.ducats else ""
        lines.append(f"**{p.name}**{qty}\n sell {_p(p.sell_total)} · buy {_p(p.buy_total)}{ducats}")
    e.description = "\n".join(lines)[:3500] or "No parts listed."
    e.add_field(name="Full set", value=(f"Cheapest seller: **{_p(report.set_lowest_sell)}**\n"
                                        f"Best buyer: **{_p(report.set_highest_buy)}**"), inline=True)
    e.add_field(name="All parts", value=(f"Buy cheapest: **{_p(report.parts_lowest_sell)}**\n"
                                         f"Sell to best buyers: **{_p(report.parts_highest_buy)}**"),
                inline=True)
    if report.total_ducats:
        e.add_field(name="Ducats", value=f"**{report.total_ducats}** for every part", inline=True)
    profit = report.flip_profit
    e.add_field(name="Verdict" + (f" ({profit:+d}{PLAT})" if profit is not None else ""),
                value=report.verdict, inline=False)
    e.set_footer(text="Online players only · " + MARKET_FOOTER)
    return e


# ------------------------------------------------------------- /wm history ---
def _pct(v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    arrow = "\U0001F4C8" if v > 2 else "\U0001F4C9" if v < -2 else "➖"
    return f"{arrow} {v:+.1f}%"


def history_embed(item: Item, long: HistorySummary, short: Optional[HistorySummary] = None,
                  *, rank: Optional[int] = None) -> nextcord.Embed:
    rank_note = f" (rank {rank})" if rank is not None else ""
    e = _embed(f"{item.name}{rank_note}: price history", url=item.url, thumb=item.thumb_url)
    if not long.points:
        e.description = "warframe.market has no closed trades recorded for this item."
        return e
    e.description = (f"**90 days** · {long.points} days with trades · "
                     f"{long.volume:,} sold\n```\n{long.sparkline}\n```")
    e.add_field(name="Prices (90 days)", inline=True, value=(
        f"Average: **{_p(long.avg_price)}**\nMedian: **{_p(long.median_price)}**\n"
        f"Low / high: {_p(long.min_price)} / {_p(long.max_price)}"))
    e.add_field(name="Trend", inline=True, value=(
        f"Last week vs week before: **{_pct(long.change_pct)}**\n"
        f"Then: {_p(long.first_price)} → latest: **{_p(long.last_price)}**"))
    if short and short.points:
        e.add_field(name="Last 48 hours", inline=False, value=(
            f"Sold **{short.volume}** · avg **{_p(short.avg_price)}** · "
            f"low {_p(short.min_price)} · high {_p(short.max_price)}"))
    if long.ranks and rank is None:
        e.add_field(name="Ranks", inline=False, value=(
            f"Showing rank {long.ranks[0]}. Ranks traded: {', '.join(map(str, long.ranks))}. "
            "Use the `rank` option for another."))
    e.set_footer(text="Closed trades reported to " + MARKET_FOOTER)
    return e


# ---------------------------------------------------------------- /wm user ---
def user_sections(user: UserProfile, orders: Sequence[Order], names: dict[str, Item],
                  *, per_page: int = 8) -> list[Section]:
    status = STATUS_ICON.get(user.status, user.status.title())
    desc = [f"{status}" + (f" · *{user.activity}*" if user.activity else "")]
    if user.about:
        desc.append(user.about[:600])
    head = _embed(user.name, url=user.url, description="\n\n".join(desc), thumb=user.avatar_url, color=BLUE)
    head.add_field(name="Reputation", value=f"**{user.reputation}**", inline=True)
    if user.mastery_rank is not None:
        head.add_field(name="Mastery rank", value=f"**{user.mastery_rank}**", inline=True)
    plat = (user.platform or "?").upper() + (" · crossplay" if user.crossplay else "")
    head.add_field(name="Platform", value=plat, inline=True)
    sells = [o for o in orders if o.type == "sell"]
    buys = [o for o in orders if o.type == "buy"]
    head.add_field(name="Listings", value=f"Selling **{len(sells)}** · Buying **{len(buys)}**", inline=False)
    if user.banned:
        head.add_field(name="⚠️", value="This account is banned on warframe.market.", inline=False)
    sections = [Section("Profile", [head], description="Reputation and status", emoji="\U0001F464")]

    def pages(subset: Sequence[Order], title: str) -> list[nextcord.Embed]:
        out = []
        for n, group in enumerate(chunk(sorted(subset, key=lambda o: _item_name(o, names)), per_page)):
            lines = []
            for o in group:
                rank = f" · R{o.rank}" if o.rank is not None else ""
                lines.append(f"**{_item_name(o, names)}**{rank} — **{o.platinum}{PLAT}** ×{o.quantity}")
            whispers = "\n".join(whisper(o, _item_name(o, names)) for o in group[:4])
            out.append(_embed(title, url=user.url, thumb=user.avatar_url,
                              description="\n".join(lines) + f"\n\n```\n{whispers[:1500]}\n```"))
        return out

    if sells:
        sections.append(Section(f"Selling ({len(sells)})", pages(sells, f"{user.name} is selling"),
                                emoji="\U0001F534"))
    if buys:
        sections.append(Section(f"Buying ({len(buys)})", pages(buys, f"{user.name} is buying"),
                                emoji="\U0001F7E2"))
    return sections


def _item_name(o: Order, names: dict[str, Item]) -> str:
    it = names.get(o.item_id)
    return it.name if it else "Unknown item"


# ------------------------------------------------------- /wm lich | sister ---
def weapon_auction_pages(weapon_name: str, auctions: Sequence[WeaponAuction], *, kind: str,
                         image: Optional[str] = None, per_page: int = 6) -> list[nextcord.Embed]:
    label = "Kuva Lich" if kind == "lich" else "Sister of Parvos"
    title = f"{weapon_name}: {label} weapons"
    if not auctions:
        return [_embed(title, description="No contracts match your filters.", thumb=image)]
    pages = []
    summary = f"**{_plural(len(auctions), 'contract')}** · cheapest first"
    for n, group in enumerate(chunk(list(auctions), per_page)):
        blocks = []
        for i, a in enumerate(group, start=n * per_page + 1):
            icon = ELEMENT_ICON.get(a.element, "")
            eph = " · ✨ Ephemera" if a.ephemera else ""
            quirk = f" · {a.quirk.replace('_', ' ').title()}" if a.quirk else ""
            price = asking_price(a)
            cost = (f"Buyout **{a.buyout_price}{PLAT}**" if a.buyout_price
                    else f"Bid **{price}{PLAT}**" if price else "Price **n/a**")
            blocks.append(f"`{i:>2}.` {icon} **{a.damage}% {a.element.title()}**{eph}{quirk}\n"
                          f"[{cost}]({a.url}) · {a.seller} · {STATUS_ICON.get(a.status, a.status)}")
        pages.append(_embed(title, description=summary + "\n\n" + "\n\n".join(blocks), thumb=image))
    return pages


# --------------------------------------------------------------- /wm ducats ---
DUCATS_PER_PLAT_GOOD = 10      # rule of thumb: ~10+ ducats per plat favours ducating


def ducats_embed(detail: ItemDetail, top: dict[str, list[Order]]) -> nextcord.Embed:
    it = detail.item
    ducats = it.ducats or 0
    sell, buy = top.get("sell") or [], top.get("buy") or []
    e = _embed(f"{it.name}: ducats vs platinum", url=it.url, thumb=it.thumb_url)
    e.add_field(name="Ducat value", value=f"**{ducats}**", inline=True)
    e.add_field(name="Cheapest seller", value=f"**{_p(sell[0].platinum)}**" if sell else "n/a", inline=True)
    e.add_field(name="Best buyer", value=f"**{_p(buy[0].platinum)}**" if buy else "n/a", inline=True)
    if not ducats:
        verdict = "This item can't be traded to Baro Ki'Teer for ducats."
    elif not buy and not sell:
        verdict = "Nobody online is trading it, so ducats are the safe choice."
    else:
        plat = buy[0].platinum if buy else sell[0].platinum
        ratio = ducats / plat
        e.add_field(name="Ducats per plat", value=f"**{ratio:.1f}**", inline=True)
        verdict = (f"Turn it into ducats: plat is cheap for its ducat value ({ratio:.1f}/p)."
                   if ratio >= DUCATS_PER_PLAT_GOOD else
                   f"Sell it for platinum: about {plat}{PLAT} is worth more than {ducats} ducats.")
    e.add_field(name="Verdict", value=verdict + "\n*Rule of thumb: 10+ ducats per plat favours ducats.*",
                inline=False)
    e.set_footer(text=MARKET_FOOTER)
    return e


def ducat_deal_pages(deals: Sequence[DucatDeal], *, per_page: int = 10) -> list[nextcord.Embed]:
    title = "Best ducats per platinum right now"
    if not deals:
        return [_embed(title, description="No online sell orders for prime parts in the latest orders.")]
    intro = ("*Cheapest online seller per prime part among the newest orders, best value first. "
             "Buy these and trade them to Baro Ki'Teer.*")
    pages = []
    for n, group in enumerate(chunk(list(deals), per_page)):
        lines = [f"{MEDALS.get(i, f'`{i:>2}.`')} **[{d.item.name}]({d.item.url})**\n"
                 f" {d.ducats}\U0001FA99 for **{d.platinum}{PLAT}** · "
                 f"**{d.ducats_per_plat:.1f}**/p · {d.seller}"
                 for i, d in enumerate(group, start=n * per_page + 1)]
        pages.append(_embed(title, description=intro + "\n\n" + "\n".join(lines)))
    return pages


# --------------------------------------------------------------- /wm trends ---
def trend_pages(rows: Sequence[TrendRow], *, per_page: int = 10) -> list[nextcord.Embed]:
    title = "Recent market trends"
    if not rows:
        return [_embed(title, description="Not enough recent orders to spot a trend.")]
    intro = "*Busiest items among the latest orders posted on warframe.market, most active first.*"
    pages = []
    for n, group in enumerate(chunk(list(rows), per_page)):
        lines = []
        for i, r in enumerate(group, start=n * per_page + 1):
            badge = MEDALS.get(i, f"`{i:>2}.`")
            prices = " · ".join(x for x in (
                f"avg sell **{r.avg_sell}{PLAT}**" if r.avg_sell is not None else "",
                f"avg buy **{r.avg_buy}{PLAT}**" if r.avg_buy is not None else "") if x)
            lines.append(f"{badge} **[{r.item.name}]({r.item.url})**\n {r.orders} orders "
                         f"({r.sells} sell / {r.buys} buy) · {prices}")
        pages.append(_embed(title, description=intro + "\n\n" + "\n".join(lines)))
    return pages


# --------------------------------------------------------------- /wm recent ---
def recent_pages(orders: Sequence[Order], names: dict[str, Item], *, side: Optional[str] = None,
                 per_page: int = 10, limit: int = 60) -> list[nextcord.Embed]:
    pool = [o for o in orders if o.item_id in names and (side is None or o.type == side)][:limit]
    title = "Newest orders" + (f" ({'selling' if side == 'sell' else 'buying'})" if side else "")
    if not pool:
        return [_embed(title, description="No recent orders.")]
    pages = []
    for group in chunk(pool, per_page):
        lines = []
        for o in group:
            it = names[o.item_id]
            kind = "\U0001F534 WTS" if o.type == "sell" else "\U0001F7E2 WTB"
            rank = f" R{o.rank}" if o.rank is not None else ""
            lines.append(f"{kind} [{it.name}]({it.url}){rank} — **{o.platinum}{PLAT}** "
                         f"×{o.quantity} · {o.seller}")
        pages.append(_embed(title, description="\n".join(lines)))
    return pages


# --------------------------------------------------------------- /wm status ---
def status_embed(status: str, minutes: int) -> nextcord.Embed:
    label = STATUS_ICON.get(status, status)
    return _embed("warframe.market status", color=GREEN,
                  description=f"You now show as **{label}** for about {minutes} minutes.")
