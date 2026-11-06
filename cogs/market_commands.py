"""/wm -- warframe.market for traders.

Anyone:
    /wm price     seller dashboard: what to list at, who buys now, competition, tips
    /wm buyers    online buyers + copy-paste whispers
    /wm sellers   cheapest online sellers (your competition)
    /wm top       best 5 online buyers and sellers (fast, supports rank filters)
    /wm item      item facts: ducats, trading tax, MR, rarity, endo, vault status, wiki link
    /wm set       is the set worth more than its parts?
    /wm history   90-day price history with trend and sparkline
    /wm ducats    ducats vs platinum for one item
    /wm ducat_deals   best ducats-per-plat prime parts on sale right now
    /wm trends    busiest items among the newest orders
    /wm recent    the newest orders posted site-wide
    /wm user      a player's profile, reputation and listings
    /wm riven     riven contracts for a weapon, filtered by stats
    /wm lich      Kuva Lich weapon contracts (element, damage, ephemera)
    /wm sister    Sister of Parvos (Tenet) weapon contracts

Owner only (OWNER_ID in .env; acts on YOUR warframe.market account):
    /wm list, /wm update_order, /wm sold, /wm unlist, /wm my_orders, /wm status
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Awaitable, Callable, Optional

import aiohttp
import nextcord
from nextcord import Interaction, SlashOption
from nextcord.ext import commands

from market.analysis import build_report
from market.client import ItemNotFound, MarketError, WarframeMarketClient
from market.embeds import (
    MARKET_FOOTER,
    closed_embed,
    ducat_deal_pages,
    ducats_embed,
    history_embed,
    item_embed,
    listed_embed,
    my_orders_sections,
    order_pages,
    recent_pages,
    removed_embed,
    report_sections,
    riven_pages,
    set_embed,
    status_embed,
    top_embed,
    trend_pages,
    updated_embed,
    user_sections,
    weapon_auction_pages,
)
from market.insights import (
    ELEMENTS,
    WeaponFilter,
    ducat_deals,
    filter_weapon_auctions,
    history_summary,
    recent_trends,
    set_report,
)
from market.riven import POLARITIES, RivenFilter, filter_rivens, stat_suggestions, unknown_stats
from market.seller import STATUSES, MarketSeller, SellerError
from ui.paginator import Section, SectionView, view_kwargs

log = logging.getLogger("warframebot.cogs.market")

USER_AND_GUILD = [nextcord.IntegrationType.guild_install, nextcord.IntegrationType.user_install]
EVERYWHERE = [
    nextcord.InteractionContextType.guild,
    nextcord.InteractionContextType.bot_dm,
    nextcord.InteractionContextType.private_channel,
]
PLATFORM_CHOICES = {"PC": "pc", "PlayStation": "ps4", "Xbox": "xbox", "Switch": "switch",
                    "Mobile": "mobile"}
ORDER_TYPE_CHOICES = {"Sell": "sell", "Buy": "buy"}
SIDE_CHOICES = {"Selling": "sell", "Buying": "buy"}
RIVEN_POLARITY_CHOICES = {p.title(): p for p in POLARITIES}
ELEMENT_CHOICES = {e.title(): e for e in ELEMENTS}
STATUS_CHOICES = {"In game": "ingame", "Online": "online", "Invisible": "invisible"}
NOT_SET_UP = "Listing isn't set up: add WFM_JWT (or WFM_EMAIL + WFM_PASSWORD) to .env."


# ------------------------------------------------------------------ options
def _item_option(desc: str = "Item name, e.g. Mirage Prime Set"):
    return SlashOption(description=desc, autocomplete=True)


def _rank_option(desc: str = "Mod/arcane rank (omit for all ranks)"):
    return SlashOption(description=desc, required=False, min_value=0, max_value=10)


def _platform_option():
    return SlashOption(description="Platform (default PC)", required=False,
                       choices=PLATFORM_CHOICES, default="pc")


def _owner_ids() -> set[int]:
    raw = os.getenv("OWNER_ID", "")
    return {int(x) for x in raw.replace(" ", "").split(",") if x.isdigit()}


def _is_owner(interaction: Interaction) -> bool:
    """This is a user-installable app, so *anyone* can run its commands. The
    listing commands act on YOUR warframe.market account and must never be open
    to everyone. OWNER_ID in .env: your Discord user id (comma-separate for several)."""
    return interaction.user is not None and interaction.user.id in _owner_ids()


async def _send_view(interaction: Interaction, sections: list[Section], *, footer: str = MARKET_FOOTER,
                     chain: bool = False, ephemeral: bool = False, placeholder: str = "Pick a section") -> None:
    view = SectionView(sections, footer_text=footer, chain=chain, select_placeholder=placeholder)
    view.message = await interaction.followup.send(**view_kwargs(view), ephemeral=ephemeral)


class MarketCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._own_session: aiohttp.ClientSession | None = None
        self._own_client: WarframeMarketClient | None = None
        self._seller: MarketSeller | None = None
        self._my_orders_cache: tuple[float, list[dict]] = (0.0, [])

    # ------------------------------------------------------------ plumbing
    @property
    def client(self) -> WarframeMarketClient:
        shared = getattr(self.bot, "market", None)
        if shared is not None:
            return shared
        if self._own_client is None:                       # cog loaded without bot.py's client
            self._own_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
            self._own_client = WarframeMarketClient(self._own_session)
        return self._own_client

    @property
    def seller(self) -> MarketSeller:
        if self._seller is None:
            self._seller = MarketSeller(self.client)
        return self._seller

    async def aclose(self) -> None:
        """Called by bot.close(): cogs aren't unloaded on a normal shutdown."""
        if self._seller is not None:
            await self._seller.close()
        if self._own_session and not self._own_session.closed:
            await self._own_session.close()

    def cog_unload(self) -> None:
        if self._own_session and not self._own_session.closed:
            self.bot.loop.create_task(self._own_session.close())
        if self._seller is not None:
            self.bot.loop.create_task(self._seller.close())

    async def _guard(self, interaction: Interaction, body: Callable[[], Awaitable[None]], *,
                     what: str, ephemeral: bool = False) -> None:
        """Run a command body, turning known failures into a short reply."""
        try:
            await body()
        except ItemNotFound as exc:
            await interaction.followup.send(str(exc) or "Not found on warframe.market.", ephemeral=True)
        except MarketError as exc:
            await interaction.followup.send(f"warframe.market problem: {exc}", ephemeral=True)
        except SellerError as exc:
            log.warning("%s failed: %s", what, exc)
            await interaction.followup.send(f"Couldn't {what}: {exc}", ephemeral=True)
        except Exception:
            log.exception("Unexpected error in /wm %s", what)
            await interaction.followup.send("Unexpected error. The bot owner can check the log.",
                                            ephemeral=True)

    async def _owner_gate(self, interaction: Interaction) -> bool:
        """True when the caller may touch the owner's account; replies otherwise."""
        if not _is_owner(interaction):
            await interaction.response.send_message(
                "Only the bot owner can manage listings.", ephemeral=True)
            return False
        if not self.seller.configured:
            await interaction.response.send_message(NOT_SET_UP, ephemeral=True)
            return False
        await interaction.response.defer(ephemeral=True)
        return True

    # --------------------------------------------------------------- group
    @nextcord.slash_command(name="wm", description="warframe.market prices, orders, contracts and players",
                            integration_types=USER_AND_GUILD, contexts=EVERYWHERE)
    async def wm(self, interaction: Interaction):
        pass  # group only

    # ============================================================ pricing
    @wm.subcommand(name="price", description="Seller dashboard: what to list at, who to sell to, competition")
    async def price(self, interaction: Interaction,
                    item: str = _item_option(), rank: int = _rank_option(),
                    platform: str = _platform_option(),
                    my_price: int = SlashOption(description="Your current/planned price, to see where it ranks",
                                                required=False, min_value=1)):
        await interaction.response.defer()

        async def body():
            it = await self.client.resolve_item(item)
            orders = await self.client.get_orders(it.slug, platform=platform)
            history = await self.client.get_history(it.slug)       # None if unavailable
            report = build_report(it.name, it.slug, orders, rank=rank, history=history, ladder_size=12)
            await _send_view(interaction, report_sections(report, it, platform=platform, my_price=my_price),
                             footer="Online/in-game players only · " + MARKET_FOOTER, chain=True,
                             placeholder="Jump to a section")
        await self._guard(interaction, body, what="price")

    async def _send_orders(self, interaction: Interaction, item_q: str, rank, platform: str, side: str):
        async def body():
            it = await self.client.resolve_item(item_q)
            orders = await self.client.get_orders(it.slug, platform=platform)
            await _send_view(interaction, [Section("Orders", order_pages(it, orders, side=side, rank=rank))],
                             footer="Whispers use warframe.market's format · " + MARKET_FOOTER)
        await self._guard(interaction, body, what=side + "ers")

    @wm.subcommand(name="buyers", description="Online buyers you can sell to right now (with whispers)")
    async def buyers(self, interaction: Interaction, item: str = _item_option(),
                     rank: int = _rank_option(), platform: str = _platform_option()):
        await interaction.response.defer()
        await self._send_orders(interaction, item, rank, platform, "buy")

    @wm.subcommand(name="sellers", description="Cheapest online sellers (your competition, or who to buy from)")
    async def sellers(self, interaction: Interaction, item: str = _item_option(),
                      rank: int = _rank_option(), platform: str = _platform_option()):
        await interaction.response.defer()
        await self._send_orders(interaction, item, rank, platform, "sell")

    @wm.subcommand(name="top", description="Best 5 online buy and sell orders for an item")
    async def top(self, interaction: Interaction, item: str = _item_option(),
                  rank: int = _rank_option("Exact mod/arcane rank"),
                  charges: int = SlashOption(description="Remaining charges (requiem mods)", required=False,
                                             min_value=0, max_value=10),
                  subtype: str = SlashOption(description="Subtype, e.g. radiant (relics) or blueprint",
                                             required=False),
                  amber_stars: int = SlashOption(description="Amber stars (Ayatan sculptures)", required=False,
                                                 min_value=0, max_value=10),
                  cyan_stars: int = SlashOption(description="Cyan stars (Ayatan sculptures)", required=False,
                                                min_value=0, max_value=10),
                  platform: str = _platform_option()):
        await interaction.response.defer()

        async def body():
            it = await self.client.resolve_item(item)
            top = await self.client.get_top_orders(it.slug, rank=rank, charges=charges,
                                                   subtype=(subtype or "").strip().lower() or None,
                                                   amber_stars=amber_stars, cyan_stars=cyan_stars,
                                                   platform=platform)
            e = top_embed(it, top, rank=rank)
            e.set_footer(text=f"{platform.upper()} · online players only · {MARKET_FOOTER}")
            await interaction.followup.send(embed=e)
        await self._guard(interaction, body, what="top")

    # ======================================================== item facts
    @wm.subcommand(name="item", description="Item facts: ducats, trading tax, mastery, endo, vault status, wiki link")
    async def item_info(self, interaction: Interaction, item: str = _item_option(),
                        platform: str = _platform_option()):
        await interaction.response.defer()

        async def body():
            it = await self.client.resolve_item(item)
            detail = await self.client.get_item(it.slug)
            try:
                top = await self.client.get_top_orders(it.slug, platform=platform)
            except MarketError:
                top = None
            await interaction.followup.send(embed=item_embed(detail, top))
        await self._guard(interaction, body, what="item")

    @wm.subcommand(name="set", description="Is a set worth more than its parts? (prime sets, mod sets, ...)")
    async def set_value(self, interaction: Interaction,
                        item: str = _item_option("A set or any part of it, e.g. Saryn Prime"),
                        platform: str = _platform_option()):
        await interaction.response.defer()

        async def body():
            it = await self.client.resolve_item(item)
            parts = await self.client.get_set(it.slug)
            if len(parts) < 2:
                await interaction.followup.send(f"**{it.name}** isn't part of a set.", ephemeral=True)
                return
            orders = {}
            for p in parts:                                   # one request each (rate limited)
                top = await self.client.get_top_orders(p.item.slug, platform=platform)
                orders[p.item.slug] = top["sell"] + top["buy"]
            report = set_report(parts, orders)
            root = next((p for p in parts if p.set_root), parts[0]).item
            await interaction.followup.send(embed=set_embed(report, root.thumb_url, root.url))
        await self._guard(interaction, body, what="set")

    @wm.subcommand(name="history", description="90-day price history, trend and recent sales")
    async def history(self, interaction: Interaction, item: str = _item_option(),
                      rank: int = _rank_option("Mod/arcane rank (default: lowest traded)")):
        await interaction.response.defer()

        async def body():
            it = await self.client.resolve_item(item)
            stats = await self.client.get_statistics(it.slug)
            long = history_summary(stats["90days"], period="90days", rank=rank)
            short = history_summary(stats["48hours"], period="48hours", rank=rank)
            await interaction.followup.send(embed=history_embed(it, long, short, rank=rank))
        await self._guard(interaction, body, what="history")

    @wm.subcommand(name="ducats", description="Ducats vs platinum: trade it to Baro or sell it?")
    async def ducats(self, interaction: Interaction, item: str = _item_option("Prime part, e.g. Ash Prime Systems"),
                     platform: str = _platform_option()):
        await interaction.response.defer()

        async def body():
            it = await self.client.resolve_item(item)
            detail = await self.client.get_item(it.slug)
            top = await self.client.get_top_orders(it.slug, platform=platform)
            await interaction.followup.send(embed=ducats_embed(detail, top))
        await self._guard(interaction, body, what="ducats")

    @wm.subcommand(name="ducat_deals", description="Cheapest prime parts per ducat on sale right now (for Baro)")
    async def ducat_deals_cmd(self, interaction: Interaction,
                              min_ducats: int = SlashOption(description="Ignore parts worth fewer ducats (default 45)",
                                                            required=False, default=45, min_value=1, max_value=100),
                              platform: str = _platform_option()):
        await interaction.response.defer()

        async def body():
            recent = await self.client.get_recent_orders(platform=platform)
            names = await self.client.item_names_by_id()
            deals = ducat_deals(recent, names, min_ducats=min_ducats)
            await _send_view(interaction, [Section("Deals", ducat_deal_pages(deals))])
        await self._guard(interaction, body, what="ducat_deals")

    # ============================================================ activity
    @wm.subcommand(name="trends", description="Busiest items among the newest orders on warframe.market")
    async def trends(self, interaction: Interaction, platform: str = _platform_option()):
        await interaction.response.defer()

        async def body():
            recent = await self.client.get_recent_orders(platform=platform)
            names = await self.client.item_names_by_id()
            await _send_view(interaction, [Section("Trends", trend_pages(recent_trends(recent, names)))],
                             footer="Based on the newest orders · " + MARKET_FOOTER)
        await self._guard(interaction, body, what="trends")

    @wm.subcommand(name="recent", description="The newest orders posted on warframe.market")
    async def recent(self, interaction: Interaction,
                     side: str = SlashOption(description="Only selling or only buying orders", required=False,
                                             choices=SIDE_CHOICES),
                     platform: str = _platform_option()):
        await interaction.response.defer()

        async def body():
            orders = await self.client.get_recent_orders(platform=platform)
            names = await self.client.item_names_by_id()
            await _send_view(interaction, [Section("Recent", recent_pages(orders, names, side=side))])
        await self._guard(interaction, body, what="recent")

    @wm.subcommand(name="user", description="A player's warframe.market profile, reputation and listings")
    async def user(self, interaction: Interaction,
                   name: str = SlashOption(description="In-game name"),
                   platform: str = _platform_option()):
        await interaction.response.defer()

        async def body():
            profile = await self.client.get_user(name)
            orders = await self.client.get_user_orders(name, profile)
            names = await self.client.item_names_by_id()
            await _send_view(interaction, user_sections(profile, orders, names))
        await self._guard(interaction, body, what="user")

    # =========================================================== contracts
    @wm.subcommand(name="riven", description="Riven mod contracts for a weapon, filtered by stats")
    async def riven(self, interaction: Interaction,
                    weapon: str = SlashOption(description="Weapon name, e.g. Acceltra", autocomplete=True),
                    stat1: str = SlashOption(description="Wanted positive stat, e.g. critical damage",
                                             required=False, autocomplete=True),
                    stat2: str = SlashOption(description="Second wanted positive stat", required=False,
                                             autocomplete=True),
                    stat3: str = SlashOption(description="Third wanted positive stat", required=False,
                                             autocomplete=True),
                    negative: str = SlashOption(description="Only rivens whose negative stat is this",
                                                required=False, autocomplete=True),
                    polarity: str = SlashOption(description="Riven polarity", required=False,
                                                choices=RIVEN_POLARITY_CHOICES),
                    rank: int = SlashOption(description="Mod rank 0-8 (omit for all)", required=False,
                                            min_value=0, max_value=8),
                    max_price: int = SlashOption(description="Highest buyout or bid to show", required=False,
                                                 min_value=1),
                    online_only: bool = SlashOption(description="Only sellers who are online (default yes)",
                                                    required=False, default=True),
                    platform: str = _platform_option()):
        await interaction.response.defer()

        async def body():
            weapons = await self.client.resolve_riven_weapons(weapon)
            auctions = []
            for it in weapons:
                auctions += await self.client.get_riven_auctions(it.slug, platform=platform)
            family = len(weapons) > 1
            label = f"{weapon.strip().title()} weapons" if family else weapons[0].name
            wanted = tuple(s.strip() for s in (stat1, stat2, stat3) if s and s.strip())
            neg = (negative or "").strip() or None
            bad = unknown_stats(auctions, [*wanted, *([neg] if neg else [])])
            if bad:
                await interaction.followup.send(
                    f"No riven for **{label}** has a stat matching: {', '.join(bad)}. "
                    "Try a shorter word, e.g. `crit`, `multishot`, `toxin`.", ephemeral=True)
                return
            if not auctions:
                await interaction.followup.send(f"No riven contracts listed for **{label}** on {platform.upper()}.")
                return
            matches = filter_rivens(auctions, RivenFilter(polarity=polarity, rank=rank, positives=wanted,
                                                          negative=neg, max_price=max_price,
                                                          online_only=online_only))
            info = None if family else await self.client.riven_weapon_info(weapons[0].slug)
            names = {it.slug: it.name for it in weapons} if family else None
            pages = riven_pages(weapons[0], matches, label=label, weapon_names=names, info=info)
            await _send_view(interaction, [Section("Rivens", pages)], footer="Cheapest first · " + MARKET_FOOTER)
        await self._guard(interaction, body, what="riven")

    async def _weapon_contracts(self, interaction: Interaction, kind: str, weapon: str, element: Optional[str],
                                min_damage: Optional[int], ephemera: Optional[bool], max_price: Optional[int],
                                online_only: bool, platform: str) -> None:
        async def body():
            entry = await self.client.resolve_weapon(kind, weapon)
            auctions = await self.client.get_weapon_auctions(kind, entry.slug, element=element,
                                                             ephemera=ephemera, platform=platform)
            matches = filter_weapon_auctions(auctions, WeaponFilter(element=element, min_damage=min_damage,
                                                                    ephemera=ephemera, max_price=max_price,
                                                                    online_only=online_only))
            pages = weapon_auction_pages(entry.name, matches, kind=kind, image=entry.image_url)
            await _send_view(interaction, [Section("Contracts", pages)],
                             footer="Cheapest first · " + MARKET_FOOTER)
        await self._guard(interaction, body, what=kind)

    @wm.subcommand(name="lich", description="Kuva Lich weapon contracts (Kuva weapons): element, damage bonus, ephemera")
    async def lich(self, interaction: Interaction,
                   weapon: str = SlashOption(description="Weapon, e.g. Kuva Bramma", autocomplete=True),
                   element: str = SlashOption(description="Bonus element", required=False, choices=ELEMENT_CHOICES),
                   min_damage: int = SlashOption(description="Lowest bonus damage % (25-60)", required=False,
                                                 min_value=25, max_value=60),
                   ephemera: bool = SlashOption(description="Comes with the ephemera?", required=False),
                   max_price: int = SlashOption(description="Highest buyout or bid", required=False, min_value=1),
                   online_only: bool = SlashOption(description="Only online sellers (default yes)",
                                                   required=False, default=True),
                   platform: str = _platform_option()):
        await interaction.response.defer()
        await self._weapon_contracts(interaction, "lich", weapon, element, min_damage, ephemera, max_price,
                                     online_only, platform)

    @wm.subcommand(name="sister", description="Sister of Parvos weapon contracts (Tenet weapons): element, damage bonus, ephemera")
    async def sister(self, interaction: Interaction,
                     weapon: str = SlashOption(description="Weapon, e.g. Tenet Arca Plasmor", autocomplete=True),
                     element: str = SlashOption(description="Bonus element", required=False, choices=ELEMENT_CHOICES),
                     min_damage: int = SlashOption(description="Lowest bonus damage % (25-60)", required=False,
                                                   min_value=25, max_value=60),
                     ephemera: bool = SlashOption(description="Comes with the ephemera?", required=False),
                     max_price: int = SlashOption(description="Highest buyout or bid", required=False, min_value=1),
                     online_only: bool = SlashOption(description="Only online sellers (default yes)",
                                                     required=False, default=True),
                     platform: str = _platform_option()):
        await interaction.response.defer()
        await self._weapon_contracts(interaction, "sister", weapon, element, min_damage, ephemera, max_price,
                                     online_only, platform)

    # ========================================================= owner only
    @wm.subcommand(name="list", description="Post a listing on warframe.market (owner only)")
    async def list_order(self, interaction: Interaction,
                         item: str = _item_option(),
                         price: int = SlashOption(description="Platinum per unit", min_value=1),
                         quantity: int = SlashOption(description="How many (default 1)", required=False,
                                                     default=1, min_value=1, max_value=9999),
                         order_type: str = SlashOption(description="Sell or buy (default sell)", required=False,
                                                       default="sell", choices=ORDER_TYPE_CHOICES),
                         rank: int = _rank_option("Mod/arcane rank (required for rankable items)"),
                         per_trade: int = SlashOption(description="Units per trade (bulk items)", required=False,
                                                      min_value=1),
                         charges: int = SlashOption(description="Remaining charges (requiem mods)", required=False,
                                                    min_value=0),
                         subtype: str = SlashOption(description="Subtype, e.g. radiant or blueprint", required=False),
                         amber_stars: int = SlashOption(description="Amber stars in the sculpture", required=False,
                                                        min_value=0),
                         cyan_stars: int = SlashOption(description="Cyan stars in the sculpture", required=False,
                                                       min_value=0),
                         visible: bool = SlashOption(description="Show the listing publicly (default yes)",
                                                     required=False, default=True),
                         platform: str = _platform_option()):
        if not await self._owner_gate(interaction):
            return

        async def body():
            it = await self.client.resolve_item(item)
            order = await self.seller.create_order(
                it, order_type=order_type, platinum=price, quantity=quantity, rank=rank,
                per_trade=per_trade, charges=charges, subtype=(subtype or "").strip().lower() or None,
                amber_stars=amber_stars, cyan_stars=cyan_stars, visible=visible, platform=platform)
            await interaction.followup.send(embed=listed_embed(order), ephemeral=True)
        await self._guard(interaction, body, what="post that listing")

    @wm.subcommand(name="update_order", description="Change one of your listings (owner only)")
    async def update_order(self, interaction: Interaction,
                           order_id: str = SlashOption(description="Order id (see /wm my_orders)", autocomplete=True),
                           platinum: int = SlashOption(description="New price", required=False, min_value=1),
                           quantity: int = SlashOption(description="New quantity", required=False, min_value=1),
                           order_type: str = SlashOption(description="Switch to buy/sell", required=False,
                                                         choices=ORDER_TYPE_CHOICES),
                           visible: bool = SlashOption(description="Show publicly", required=False),
                           rank: int = _rank_option("New rank"),
                           per_trade: int = SlashOption(description="Units per trade", required=False, min_value=1),
                           charges: int = SlashOption(description="Remaining charges", required=False, min_value=0),
                           amber_stars: int = SlashOption(description="Amber stars", required=False, min_value=0),
                           cyan_stars: int = SlashOption(description="Cyan stars", required=False, min_value=0),
                           platform: str = _platform_option()):
        if not await self._owner_gate(interaction):
            return
        changes = {"type": order_type, "platinum": platinum, "quantity": quantity, "visible": visible,
                   "rank": rank, "perTrade": per_trade, "charges": charges, "amberStars": amber_stars,
                   "cyanStars": cyan_stars}

        async def body():
            updated = await self.seller.update_order(order_id.strip(), platform=platform, **changes)
            changed = frozenset(k for k, v in changes.items() if v is not None)
            await interaction.followup.send(embed=updated_embed(updated, changed), ephemeral=True)
        await self._guard(interaction, body, what="update that listing")

    @wm.subcommand(name="sold", description="Mark a listing as sold/bought so it counts in statistics (owner only)")
    async def sold(self, interaction: Interaction,
                   order_id: str = SlashOption(description="Order id (see /wm my_orders)", autocomplete=True),
                   quantity: int = SlashOption(description="How many were traded (default all)", required=False,
                                               min_value=1),
                   platform: str = _platform_option()):
        if not await self._owner_gate(interaction):
            return

        async def body():
            closed = await self.seller.close_order(order_id.strip(), quantity, platform=platform)
            await interaction.followup.send(embed=closed_embed(closed), ephemeral=True)
        await self._guard(interaction, body, what="close that listing")

    @wm.subcommand(name="unlist", description="Remove one of your listings (owner only)")
    async def unlist_order(self, interaction: Interaction,
                           order_id: str = SlashOption(description="Order id (see /wm my_orders)", autocomplete=True),
                           platform: str = _platform_option()):
        if not await self._owner_gate(interaction):
            return

        async def body():
            await self.seller.delete_order(order_id.strip(), platform=platform)
            await interaction.followup.send(embed=removed_embed(order_id.strip()), ephemeral=True)
        await self._guard(interaction, body, what="remove that listing")

    @wm.subcommand(name="my_orders", description="All of your current listings (owner only)")
    async def my_orders(self, interaction: Interaction):
        if not await self._owner_gate(interaction):
            return

        async def body():
            orders = await self.seller.get_my_orders()
            await _send_view(interaction, my_orders_sections(orders), ephemeral=True,
                             placeholder="Filter orders")
        await self._guard(interaction, body, what="load your listings")

    @wm.subcommand(name="status", description="Show as in game / online / invisible on warframe.market (owner only)")
    async def status(self, interaction: Interaction,
                     status: str = SlashOption(description="Status to show", choices=STATUS_CHOICES),
                     minutes: int = SlashOption(description="How long (default 60)", required=False, default=60,
                                                min_value=5, max_value=720)):
        if not await self._owner_gate(interaction):
            return

        async def body():
            if status not in STATUSES:
                raise SellerError("unknown status")
            try:
                await self.seller.set_status(status, minutes=minutes)
            except asyncio.TimeoutError as exc:
                raise SellerError("the warframe.market websocket didn't answer") from exc
            await interaction.followup.send(embed=status_embed(status, minutes), ephemeral=True)
        await self._guard(interaction, body, what="set your status")

    # ======================================================== autocomplete
    async def _item_ac(self, interaction: Interaction, current: str):
        try:
            items = await self.client.search_items(current, 25)
        except Exception:
            items = []
        # label shown == value received; the name resolves back through resolve_item()
        await interaction.response.send_autocomplete({i.name[:100]: i.name[:100] for i in items})

    for _cmd in (price, buyers, sellers, top, item_info, set_value, history, ducats, list_order):
        _cmd.on_autocomplete("item")(_item_ac)

    @riven.on_autocomplete("weapon")
    async def _riven_ac(self, interaction: Interaction, current: str):
        try:
            weapons = await self.client.search_riven_weapons(current, 25)
        except Exception:
            weapons = []
        await interaction.response.send_autocomplete({w.name[:100]: w.name[:100] for w in weapons})

    async def _stat_ac(self, interaction: Interaction, current: str):
        try:
            slugs = await self.client.get_riven_attributes()
        except Exception:
            slugs = []
        names = stat_suggestions(current, slugs, 25)
        await interaction.response.send_autocomplete({n[:100]: n[:100] for n in names})

    for _opt in ("stat1", "stat2", "stat3", "negative"):
        riven.on_autocomplete(_opt)(_stat_ac)

    async def _lich_ac(self, interaction: Interaction, current: str):
        await self._weapon_ac(interaction, "lich", current)

    async def _sister_ac(self, interaction: Interaction, current: str):
        await self._weapon_ac(interaction, "sister", current)

    async def _weapon_ac(self, interaction: Interaction, kind: str, current: str):
        try:
            found = await self.client.search_weapons(kind, current, 25)
        except Exception:
            found = []
        await interaction.response.send_autocomplete({w.name[:100]: w.name[:100] for w in found})

    lich.on_autocomplete("weapon")(_lich_ac)
    sister.on_autocomplete("weapon")(_sister_ac)

    async def _order_id_ac(self, interaction: Interaction, current: str):
        """Owner's own orders, labelled by item, so ids never need copy-pasting."""
        if not _is_owner(interaction) or not self.seller.configured:
            await interaction.response.send_autocomplete({})
            return
        loaded, orders = self._my_orders_cache
        if time.monotonic() - loaded > 30:                  # don't hit the API on every keystroke
            try:
                orders = await asyncio.wait_for(self.seller.get_my_orders(), timeout=2.5)
                self._my_orders_cache = (time.monotonic(), orders)
            except Exception:
                orders = []
        q = (current or "").lower()
        out = {}
        for o in orders:
            d = o["order_data"]
            label = f"{d.get('type', '?').upper()} {o['item']} · {d.get('platinum')}p ×{d.get('quantity')}"
            if q and q not in label.lower() and q not in str(d.get("id")):
                continue
            out[label[:100]] = str(d.get("id"))
            if len(out) == 25:
                break
        await interaction.response.send_autocomplete(out)

    for _cmd in (update_order, sold, unlist_order):
        _cmd.on_autocomplete("order_id")(_order_id_ac)
    del _cmd, _opt


def setup(bot: commands.Bot) -> None:
    bot.add_cog(MarketCommands(bot))
