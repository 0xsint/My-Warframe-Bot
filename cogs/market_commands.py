"""/wm  -- warframe.market tools aimed at people who SELL.

    /wm price   item:<..> [rank] [platform] [my_price]   pricing dashboard
    /wm buyers  item:<..> [rank] [platform]              online buyers + copy-paste whispers
    /wm sellers item:<..> [rank] [platform]              your online competition

Load with:   bot.load_extension("cogs.market_commands")
"""
from __future__ import annotations

import aiohttp
import nextcord
from nextcord.ext import commands

from market.analysis import build_report
from market.client import ItemNotFound, WarframeMarketClient, MarketError, PLATFORMS
from market.embeds import report_embed, whisper_embed

USER_AND_GUILD = [nextcord.IntegrationType.guild_install, nextcord.IntegrationType.user_install]
EVERYWHERE = [
    nextcord.InteractionContextType.guild,
    nextcord.InteractionContextType.bot_dm,
    nextcord.InteractionContextType.private_channel,
]
PLATFORM_CHOICES = {"PC": "pc", "PlayStation": "ps4", "Xbox": "xbox", "Switch": "switch"}


def _item_option():
    return nextcord.SlashOption(description="Item name, e.g. Mirage Prime Set", autocomplete=True)


def _rank_option():
    return nextcord.SlashOption(description="Mod/arcane rank (0 = unranked, omit for all)",
                                required=False, min_value=0, max_value=10)


def _platform_option():
    return nextcord.SlashOption(description="Platform (default PC)", required=False,
                                choices=PLATFORM_CHOICES, default="pc")


class MarketCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self._client: WarframeMarketClient | None = None

    @property
    def client(self) -> WarframeMarketClient:
        if self._client is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
            self._client = WarframeMarketClient(self._session)
        return self._client

    def cog_unload(self) -> None:
        if self._session and not self._session.closed:
            self.bot.loop.create_task(self._session.close())

    # --------------------------------------------------------------- group
    @nextcord.slash_command(name="wm", description="warframe.market tools for sellers",
                            integration_types=USER_AND_GUILD, contexts=EVERYWHERE)
    async def wm(self, interaction: nextcord.Interaction):
        pass  # group only

    async def _fetch(self, interaction, item_q: str, platform: str):
        """Resolve the item and pull its orders; sends the error itself and returns None on failure."""
        try:
            item = await self.client.resolve_item(item_q)
            orders = await self.client.get_orders(item.slug, platform=platform)
            return item, orders
        except ItemNotFound:
            await interaction.followup.send(f"No tradable item matches **{item_q}**.")
        except MarketError as exc:
            await interaction.followup.send(f"warframe.market problem: {exc}")
        return None

    @wm.subcommand(name="price", description="Seller dashboard: what to list at, who to sell to, competition")
    async def price(self, interaction: nextcord.Interaction,
                    item: str = _item_option(), rank: int = _rank_option(),
                    platform: str = _platform_option(),
                    my_price: int = nextcord.SlashOption(
                        description="Your current/planned price, to see where it ranks",
                        required=False, min_value=1)):
        await interaction.response.defer()
        got = await self._fetch(interaction, item, platform)
        if not got:
            return
        it, orders = got
        history = await self.client.get_history(it.slug)       # None if unavailable
        report = build_report(it.name, it.slug, orders, rank=rank, history=history)
        await interaction.followup.send(embed=report_embed(report, it, platform=platform, my_price=my_price))

    @wm.subcommand(name="buyers", description="Online buyers you can sell to right now (with whispers)")
    async def buyers(self, interaction: nextcord.Interaction,
                     item: str = _item_option(), rank: int = _rank_option(),
                     platform: str = _platform_option()):
        await interaction.response.defer()
        got = await self._fetch(interaction, item, platform)
        if got:
            it, orders = got
            await interaction.followup.send(embed=whisper_embed(it, orders, side="buy", rank=rank))

    @wm.subcommand(name="sellers", description="Cheapest online sellers (your competition)")
    async def sellers(self, interaction: nextcord.Interaction,
                      item: str = _item_option(), rank: int = _rank_option(),
                      platform: str = _platform_option()):
        await interaction.response.defer()
        got = await self._fetch(interaction, item, platform)
        if got:
            it, orders = got
            await interaction.followup.send(embed=whisper_embed(it, orders, side="sell", rank=rank))

    # --------------------------------------------------------- autocomplete
    async def _suggest(self, interaction: nextcord.Interaction, current: str) -> None:
        try:
            items = await self.client.search_items(current, 25)
        except Exception:
            items = []
        # label shown == value received; the name resolves back through resolve_item()
        await interaction.response.send_autocomplete({i.name[:100]: i.name[:100] for i in items})

    @price.on_autocomplete("item")
    async def _price_ac(self, interaction: nextcord.Interaction, current: str):
        await self._suggest(interaction, current)

    @buyers.on_autocomplete("item")
    async def _buyers_ac(self, interaction: nextcord.Interaction, current: str):
        await self._suggest(interaction, current)

    @sellers.on_autocomplete("item")
    async def _sellers_ac(self, interaction: nextcord.Interaction, current: str):
        await self._suggest(interaction, current)


def setup(bot: commands.Bot) -> None:
    bot.add_cog(MarketCommands(bot))
