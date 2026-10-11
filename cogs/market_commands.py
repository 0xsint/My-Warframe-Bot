"""/wfm slash commands backed by warframe.market.

    /wfm price      item [rank] [platform]
    /wfm compare    item1 item2 [item3] [item4] [rank] [platform]
    /wfm available  item [max_price] [quantity] [rank] [platform]
    /wfm set        item [platform]
    /wfm trend      item [rank] [platform]

A *separate* top-level group from /wf: nextcord can't extend one cog's group
from another cog. Registered for both guild installs and user installs, like
the existing /wf commands.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

import nextcord
from nextcord import IntegrationType, InteractionContextType, SlashOption
from nextcord.ext import commands

from market.analysis import (
    SetBreakdown,
    SetPart,
    build_summary,
    build_trend,
    plan_purchase,
)
from market.client import (
    AmbiguousItem,
    Item,
    ItemNotFound,
    MarketError,
    MarketUnavailable,
    WarframeMarketClient,
)
from market import embeds

log = logging.getLogger("warframebot.market.cog")

RANK_CHOICES = {"Any rank": "any", "Unranked only": "unranked", "Max rank only": "max"}
PLATFORM_CHOICES = {"PC": "pc", "PlayStation": "ps4", "Xbox": "xbox", "Switch": "switch", "Mobile": "mobile"}


def rank_option() -> str:
    return SlashOption(
        description="Mods/arcanes only: which ranks to include",
        choices=RANK_CHOICES,
        default="any",
    )


def platform_option() -> str:
    return SlashOption(description="Platform (default: PC)", choices=PLATFORM_CHOICES, default="pc")


def item_option(description: str = "Item name (start typing)", *, required: bool = True) -> str:
    if required:
        return SlashOption(description=description, autocomplete=True)
    return SlashOption(description=description, autocomplete=True, required=False, default=None)


class MarketCommands(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ---- helpers ---------------------------------------------------------- #
    @property
    def market(self) -> WarframeMarketClient:
        client = getattr(self.bot, "market", None)
        if client is None:
            raise MarketUnavailable("The market client isn't ready yet; try again in a moment.")
        return client

    async def _item(self, query: str) -> Item:
        return (await self.market.load_items()).resolve(query)

    async def _respond(
        self, interaction: nextcord.Interaction, build: Callable[[], Awaitable[nextcord.Embed]]
    ) -> None:
        """Defer, run ``build``, and turn every expected failure into a clean message."""
        await interaction.response.defer()
        try:
            embed = await build()
        except AmbiguousItem as exc:
            names = "\n".join(f"• {c.name}" for c in exc.candidates)
            msg = f"**{exc.query}** matches several items. Pick one from the autocomplete list:\n{names}"
        except ItemNotFound as exc:
            hint = ""
            if exc.suggestions:
                hint = "\nDid you mean: " + ", ".join(s.name for s in exc.suggestions[:5])
            msg = f"No tradable item found for **{exc.query}**.{hint}"
        except MarketUnavailable as exc:
            msg = f"⚠️ {exc}"
        except MarketError as exc:
            msg = f"⚠️ warframe.market error: {exc}"
        except Exception:  # noqa: BLE001 - never leave an interaction hanging
            log.exception("Unhandled error in /wfm command")
            msg = "⚠️ Something went wrong while checking the market."
        else:
            await interaction.followup.send(embed=embed)
            return
        await interaction.followup.send(msg, ephemeral=True)

    async def _summary(self, item: Item, platform: str, rank: str):
        orders = await self.market.get_orders(item.slug, platform)
        return build_summary(item, orders, rank_mode=rank)

    async def _autocomplete(self, interaction: nextcord.Interaction, query: str) -> None:
        try:
            index = await asyncio.wait_for(self.market.load_items(), timeout=2.0)
        except Exception:  # noqa: BLE001 - autocomplete must never raise
            await interaction.response.send_autocomplete([])
            return
        choices = {item.name[:100]: item.slug for item in index.search(query, limit=25)}
        await interaction.response.send_autocomplete(choices)

    # ---- command group ---------------------------------------------------- #
    @nextcord.slash_command(
        name="wfm",
        description="warframe.market prices, availability and trends",
        integration_types=[IntegrationType.guild_install, IntegrationType.user_install],
        contexts=[
            InteractionContextType.guild,
            InteractionContextType.bot_dm,
            InteractionContextType.private_channel,
        ],
    )
    async def wfm(self, interaction: nextcord.Interaction) -> None:  # pragma: no cover
        pass

    # -- /wfm price --------------------------------------------------------- #
    @wfm.subcommand(name="price", description="Live price check: cheapest sellers, top buyers, spread")
    async def price(
        self,
        interaction: nextcord.Interaction,
        item: str = item_option(),
        rank: str = rank_option(),
        platform: str = platform_option(),
    ) -> None:
        async def build() -> nextcord.Embed:
            it = await self._item(item)
            summary, stats = await asyncio.gather(
                self._summary(it, platform, rank),
                self.market.get_stats(it.slug, platform),
                return_exceptions=True,
            )
            if isinstance(summary, BaseException):
                raise summary
            # History is a bonus: v1 stats failing must not break the price check.
            trend = None if isinstance(stats, BaseException) else build_trend(it, stats, rank)
            return embeds.price_embed(summary, platform, trend)

        await self._respond(interaction, build)

    # -- /wfm compare ------------------------------------------------------- #
    @wfm.subcommand(name="compare", description="Compare live prices of 2-4 items side by side")
    async def compare(
        self,
        interaction: nextcord.Interaction,
        item1: str = item_option("First item"),
        item2: str = item_option("Second item"),
        item3: str = item_option("Third item (optional)", required=False),
        item4: str = item_option("Fourth item (optional)", required=False),
        rank: str = rank_option(),
        platform: str = platform_option(),
    ) -> None:
        async def build() -> nextcord.Embed:
            index = await self.market.load_items()
            resolved = []
            for q in (item1, item2, item3, item4):
                if q:
                    it = index.resolve(q)
                    if it not in resolved:
                        resolved.append(it)
            if len(resolved) < 2:
                raise MarketError("Pick at least two different items to compare.")
            summaries = await asyncio.gather(*(self._summary(it, platform, rank) for it in resolved))
            return embeds.compare_embed(list(summaries), platform)

        await self._respond(interaction, build)

    # -- /wfm available ----------------------------------------------------- #
    @wfm.subcommand(name="available", description="Is it for sale right now? Optionally within a budget")
    async def available(
        self,
        interaction: nextcord.Interaction,
        item: str = item_option(),
        max_price: int = SlashOption(
            description="Max platinum per unit", required=False, default=None, min_value=1
        ),
        quantity: int = SlashOption(
            description="How many you need (default 1)", required=False, default=1, min_value=1, max_value=99
        ),
        rank: str = rank_option(),
        platform: str = platform_option(),
    ) -> None:
        async def build() -> nextcord.Embed:
            it = await self._item(item)
            summary = await self._summary(it, platform, rank)
            plan = plan_purchase(summary.sells, quantity, max_price)
            return embeds.availability_embed(summary, plan, max_price, platform)

        await self._respond(interaction, build)

    # -- /wfm set ----------------------------------------------------------- #
    @wfm.subcommand(name="set", description="Compare a Prime set's price with buying its parts")
    async def set_(
        self,
        interaction: nextcord.Interaction,
        item: str = item_option("Any set or part (e.g. 'Ash Prime Set')"),
        platform: str = platform_option(),
    ) -> None:
        async def build() -> nextcord.Embed:
            it = await self._item(item)
            members = await self.market.get_set(it.slug)
            root = next((m for m in members if m.set_root), None)
            parts = [m for m in members if not m.set_root]
            if root is None or not parts:
                raise MarketError(f"**{it.name}** isn't part of a tradable set.")
            summaries = await asyncio.gather(
                *(self._summary(m, platform, "any") for m in [root, *parts])
            )
            breakdown = SetBreakdown(
                root=SetPart(root, 1, summaries[0]),
                parts=[SetPart(m, m.quantity_in_set, s) for m, s in zip(parts, summaries[1:])],
            )
            return embeds.set_embed(breakdown, platform)

        await self._respond(interaction, build)

    # -- /wfm trend --------------------------------------------------------- #
    @wfm.subcommand(name="trend", description="Price history from closed trades (7d/30d/90d)")
    async def trend(
        self,
        interaction: nextcord.Interaction,
        item: str = item_option(),
        rank: str = rank_option(),
        platform: str = platform_option(),
    ) -> None:
        async def build() -> nextcord.Embed:
            it = await self._item(item)
            stats = await self.market.get_stats(it.slug, platform)
            t = build_trend(it, stats, rank)
            if t is None:
                raise MarketError(f"No trade history available for **{it.name}**.")
            return embeds.trend_embed(t, platform)

        await self._respond(interaction, build)

    # ---- autocomplete wiring --------------------------------------------- #
    @price.on_autocomplete("item")
    async def _ac_price(self, interaction: nextcord.Interaction, query: str) -> None:
        await self._autocomplete(interaction, query)

    @compare.on_autocomplete("item1")
    async def _ac_c1(self, interaction: nextcord.Interaction, query: str) -> None:
        await self._autocomplete(interaction, query)

    @compare.on_autocomplete("item2")
    async def _ac_c2(self, interaction: nextcord.Interaction, query: str) -> None:
        await self._autocomplete(interaction, query)

    @compare.on_autocomplete("item3")
    async def _ac_c3(self, interaction: nextcord.Interaction, query: str) -> None:
        await self._autocomplete(interaction, query)

    @compare.on_autocomplete("item4")
    async def _ac_c4(self, interaction: nextcord.Interaction, query: str) -> None:
        await self._autocomplete(interaction, query)

    @available.on_autocomplete("item")
    async def _ac_avail(self, interaction: nextcord.Interaction, query: str) -> None:
        await self._autocomplete(interaction, query)

    @set_.on_autocomplete("item")
    async def _ac_set(self, interaction: nextcord.Interaction, query: str) -> None:
        await self._autocomplete(interaction, query)

    @trend.on_autocomplete("item")
    async def _ac_trend(self, interaction: nextcord.Interaction, query: str) -> None:
        await self._autocomplete(interaction, query)


def setup(bot: commands.Bot) -> None:
    bot.add_cog(MarketCommands(bot))
