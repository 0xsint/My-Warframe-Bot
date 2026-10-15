"""/wm  -- warframe.market tools aimed at people who SELL.

    /wm price   item:<..> [rank] [platform] [my_price]   pricing dashboard
    /wm buyers  item:<..> [rank] [platform]              online buyers + copy-paste whispers
    /wm sellers item:<..> [rank] [platform]              your online competition
    /wm list    item:<..> price:<..> [...]               post a listing   (owner only)
    /wm unlist  order_id:<..>                            remove a listing (owner only)

Load with:   bot.load_extension("cogs.market_commands")
"""
from __future__ import annotations

import logging
import os

import aiohttp
import nextcord
from nextcord.ext import commands

from market.analysis import build_report
from market.client import ItemNotFound, WarframeMarketClient, MarketError, PLATFORMS
from market.embeds import report_embed, whisper_embed
from market.seller import MarketSeller, SellerError

log = logging.getLogger("warframebot.cogs.market")

USER_AND_GUILD = [nextcord.IntegrationType.guild_install, nextcord.IntegrationType.user_install]
EVERYWHERE = [
    nextcord.InteractionContextType.guild,
    nextcord.InteractionContextType.bot_dm,
    nextcord.InteractionContextType.private_channel,
]
PLATFORM_CHOICES = {"PC": "pc", "PlayStation": "ps4", "Xbox": "xbox", "Switch": "switch"}
ORDER_TYPE_CHOICES = {"Sell": "sell", "Buy": "buy"}


def _item_option():
    return nextcord.SlashOption(description="Item name, e.g. Mirage Prime Set", autocomplete=True)


def _rank_option():
    return nextcord.SlashOption(description="Mod/arcane rank (0 = unranked, omit for all)",
                                required=False, min_value=0, max_value=10)


def _platform_option():
    return nextcord.SlashOption(description="Platform (default PC)", required=False,
                                choices=PLATFORM_CHOICES, default="pc")


def _is_owner(interaction: nextcord.Interaction) -> bool:
    """Only the account owner may post/remove listings. This is a user-installable
    app, so *anyone* can run its commands; the listing commands act on YOUR
    warframe.market account and must never be open to everyone.
    OWNER_ID in .env: your Discord user id (comma-separate for several)."""
    raw = os.getenv("OWNER_ID", "")
    allowed = {int(x) for x in raw.replace(" ", "").split(",") if x.isdigit()}
    return interaction.user.id in allowed


class MarketCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self._client: WarframeMarketClient | None = None
        self._seller: MarketSeller | None = None

    @property
    def client(self) -> WarframeMarketClient:
        if self._client is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
            self._client = WarframeMarketClient(self._session)
        return self._client

    @property
    def seller(self) -> MarketSeller:
        if self._seller is None:
            self._seller = MarketSeller()
        return self._seller

    def cog_unload(self) -> None:
        if self._session and not self._session.closed:
            self.bot.loop.create_task(self._session.close())
        if self._seller is not None:
            self.bot.loop.create_task(self._seller.close())

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

    # ------------------------------------------------- posting (owner only)
    @wm.subcommand(name="list", description="Post a listing on warframe.market (owner only)")
    async def list_order(self, interaction: nextcord.Interaction,
                         item: str = _item_option(),
                         price: int = nextcord.SlashOption(description="Platinum per unit", min_value=1),
                         quantity: int = nextcord.SlashOption(description="How many (default 1)",
                                                              required=False, default=1,
                                                              min_value=1, max_value=9999),
                         order_type: str = nextcord.SlashOption(description="Sell or buy (default sell)",
                                                                required=False, default="sell",
                                                                choices=ORDER_TYPE_CHOICES),
                         pertrade: int = nextcord.SlashOption(description="How much you want per trade?", required=False),
                         mod_charges: int = nextcord.SlashOption(description="Charges for the mod", required=False),
                         subtype: str = nextcord.SlashOption(description="Subtype", required=False),
                         amberstars: int = nextcord.SlashOption(description="How many amber stars are in the sculpture?", required=False),
                         cyanstars: int = nextcord.SlashOption(description="How many Cyan stars in the sculpture?", required=False),
                         rank: int = _rank_option(),
                         platform: str = _platform_option(),
                         visible: bool = nextcord.SlashOption(description="Show the listing publicly (default yes)",
                                                              required=False, default=True)):
        if not _is_owner(interaction):
            await interaction.response.send_message("You're not allowed to post listings.", ephemeral=True)
            return
        if not self.seller.configured:
            await interaction.response.send_message(
                "Listing isn't set up: add WFM_JWT (or WFM_EMAIL + WFM_PASSWORD) to .env.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            it = await self.client.resolve_item(item) # Resolve the item to get its slug and other details
            order = await self.seller.create_order(
                it.slug, order_type=order_type, platinum=price, quantity=quantity,
                rank=rank, visible=visible, platform=platform, perTrade=pertrade, mod_charges=mod_charges, subtype=subtype, amberStars=amberstars, cyanStars=cyanstars
            ) # Create the order on the Warframe Market
        except ItemNotFound:
            await interaction.followup.send(f"No tradable item matches **{item}**.", ephemeral=True)
            return
        except (SellerError, MarketError) as exc:
            log.warning("Listing failed: %s", exc)
            await interaction.followup.send(f"Couldn't post that listing: {exc}", ephemeral=True) # Inform the user that the listing could not be posted due to an error
            return
        except Exception:
            log.exception("Unexpected error while posting a listing")
            await interaction.followup.send("Unexpected error while posting - check the log.", ephemeral=True) # Inform the user that an unexpected error occurred while posting the listing
            return
        # Prepare the embed message to display the listed order details
        embed = nextcord.Embed(
            title=f"Listed: {order.item_name}",
            description=(
                f"**{order.order_type.title()}** x{order.quantity} at **{order.platinum}p** each"
                + (f" (rank {order.rank})" if order.rank is not None else "")
                + ("" if order.visible else "\n*Hidden - only you can see it.*")
            ),
            color=nextcord.Color.green(), 
        ) # Create the embed message for the listed order
        # Set the footer for the embed message with the order ID and instructions for unlisting
        embed.set_footer(text=f"Order id: {order.id}  (use /wm unlist to remove)")
        await interaction.followup.send(embed=embed, ephemeral=False)
    @wm.subcommand(name="ducats_compare", description="Compare item prices in ducats and platinum")
    async def ducats_compare(self, interaction: nextcord.Interaction, item: str = _item_option()): 
        # Implementation for comparing item prices in ducats and platinum goes here
        i = await self.seller.compare_ducats(item)
        embed = nextcord.Embed(
            title="Ducats vs Platinum Comparison",
            description=f"**{i['name']}**: {i['ducats']} ducats, {int(i['average_platinum'])}p average",
            color=nextcord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=False)
    @wm.subcommand(name="trends", description="Analyze recent market trends based on recent orders")
    async def trends(self, interaction: nextcord.Interaction):
        await interaction.response.defer()
        await interaction.followup.send("Analyzing recent orders...This may take a while..", ephemeral=True)
        try:
            trends = await self.seller.analyze_recent_orders()
            log.info("Analyzed recent orders and generated trends.")
            embed = nextcord.Embed(
                title="Recent Market Trends",
                description="\n".join(
                    f"**{item_name}**: {stats['total_quantity']} sold at an average of {stats['average_platinum']}p Count: {stats['count']}\r\n"
                    for item_name, stats in trends.items()
                ),
                color=nextcord.Color.gold(),
            )
            await interaction.followup.send(embed=embed, ephemeral=False)
        except SellerError as se:
            await interaction.followup.send(f"An error occurred while analyzing trends: {se}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"An unexpected error occurred: {e}", ephemeral=True)
            log.error(f"An unexpected error occurred: {e}")
    @wm.subcommand(name="update_order", description="Update one of your listings by order id (owner only)")
    async def update_order(self, interaction: nextcord.Interaction,
                           order_id: str = nextcord.SlashOption(description="Order id shown when you listed it", required=True),
                           order_type = nextcord.SlashOption(description="New order type (buy/sell)", required=True, choices=ORDER_TYPE_CHOICES),
                           quantity: int = nextcord.SlashOption(description="New quantity for the order", required=False),
                           platinum: int = nextcord.SlashOption(description="New platinum price for the order", required=False),
                           platform: str = _platform_option(),
                           per_item: bool = nextcord.SlashOption(description="Whether the price is per trade", required=False),
                           visible: bool = nextcord.SlashOption(description="Whether the order is visible", required=False),
                           charges: int = nextcord.SlashOption(description="New number of charges for the order", required=False),
                           rank: int = nextcord.SlashOption(description="New rank for the order", required=False),
                           ambers: int = nextcord.SlashOption(description="New number of ambers for the order", required=False),
                           cyans: int = nextcord.SlashOption(description="New number of cyans for the order", required=False),
                           ): # Define the parameters for the update_order subcommand
        if not _is_owner(interaction):
            await interaction.response.send_message("You're not allowed to update listings.", ephemeral=True)
            return
        if not self.seller.configured:
            await interaction.response.send_message(
                "Listing isn't set up: add WFM_JWT (or WFM_EMAIL + WFM_PASSWORD) to .env.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.seller.update_order(order_id.strip(), order_type=order_type, quantity=quantity, platinum=platinum, platform=platform, per_item=per_item, visible=visible, charges=charges, rank=rank, ambers=ambers, cyans=cyans) # Attempt to update the order with the specified ID
        except SellerError as exc:
            await interaction.followup.send(f"Couldn't update that listing: {exc}", ephemeral=True)
            return
        changes = None
        for order in await self.seller.get_my_orders():
            if order.get("order_data", {}).get("id") == order_id.strip():
                changes = order
                break
        embed = nextcord.Embed(title="Order Updated", color=nextcord.Color.gold())
        embed.add_field(name="Order ID", value=order_id.strip(), inline=False)
        if changes is None:
            await interaction.followup.send("Couldn't find the updated order.", ephemeral=True)
            return
        embed.add_field(name="Changes", value="## The following fields have been updated:", inline=False)
        if changes.get("order_data").get("order_type", None) is not None and changes.get("order_data").get("order_type") != order_type:
            embed.add_field(name="Order Type", value=f"{changes.get('order_data').get('order_type')}", inline=False)
        if changes.get("order_data").get("quantity", None) is not None and changes.get("order_data").get("quantity") != quantity:
            embed.add_field(name="Quantity", value=f"{changes.get('order_data').get('quantity')}", inline=False)
        if changes.get("order_data").get("platinum", None) is not None and changes.get("order_data").get("platinum") != platinum:
            embed.add_field(name="Platinum", value=f"{changes.get('order_data').get('platinum')}", inline=False)
        if changes.get("order_data").get("platform", None) is not None and changes.get("order_data").get("platform") != platform:
            embed.add_field(name="Platform", value=f"{changes.get('order_data').get('platform')}", inline=False)
        if changes.get("order_data").get("perTrade", None) is not None and changes.get("order_data").get("perTrade") != per_item:
            embed.add_field(name="Per Item", value=f"{changes.get('order_data').get('perTrade')}", inline=False)
        if changes.get("order_data").get("visible", None) is not None and changes.get("order_data").get("visible") != visible:
            embed.add_field(name="Visible", value=f"{changes.get('order_data').get('visible')}", inline=False)
        if changes.get("order_data").get("charges", None) is not None and changes.get("order_data").get("charges") != charges:
            embed.add_field(name="Charges", value=f"{changes.get('order_data').get('charges')}", inline=False)
        if changes.get("order_data").get("rank", None) is not None and changes.get("order_data").get("rank") != rank:
            embed.add_field(name="Rank", value=f"{changes.get('order_data').get('rank')}", inline=False)
        if changes.get("order_data").get("amberStars", None) is not None and changes.get("order_data").get("amberStars") != ambers:
            embed.add_field(name="Amber Stars", value=f"{changes.get('order_data').get('amberStars')}", inline=False)
        if changes.get("order_data").get("cyanStars", None) is not None and changes.get("order_data").get("cyanStars", None) != cyans:
            embed.add_field(name="Cyan Stars", value=f"{changes.get('order_data').get('cyanStars')}", inline=False)
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    @wm.subcommand(name="unlist", description="Remove one of your listings by order id (owner only)")
    async def unlist_order(self, interaction: nextcord.Interaction,
                           order_id: str = nextcord.SlashOption(description="Order id shown when you listed it"),
                           platform: str = _platform_option()):
        if not _is_owner(interaction):
            await interaction.response.send_message("You're not allowed to remove listings.", ephemeral=True)
            return
        if not self.seller.configured:
            await interaction.response.send_message(
                "Listing isn't set up: add WFM_JWT (or WFM_EMAIL + WFM_PASSWORD) to .env.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await self.seller.delete_order(order_id.strip(), platform=platform) # Attempt to delete the order with the specified ID
        except SellerError as exc:
            await interaction.followup.send(f"Couldn't remove that listing: {exc}", ephemeral=True)
            return
        await interaction.followup.send(f"Removed order `{order_id.strip()}`.", ephemeral=True)
    @wm.subcommand(name="my_orders", description="List all of your current orders")
    async def my_orders(self, interaction: nextcord.Interaction):
        if not _is_owner(interaction):
            await interaction.response.send_message("You are not allowed to view orders", ephemeral=True)
            return
        if not self.seller.configured:
            await interaction.response.send_message(
                "Listing isn't set up: add WFM_JWT (or WFM_EMAIL + WFM_PASSWORD) to .env.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            order_data = ""
            orders = await self.seller.get_my_orders()
            embed = nextcord.Embed(title=f"{len(orders)} orders!", color=0xE6B422)
            embed.description = "# Below are the results of the orders"
            for result in orders:
                order = result.get("order_data")
                embed.description += f"\r\n[{order.get('type')}] {result.get('item')} - Order ID: ``{order.get('id')}`` Platinum: ``{order.get('platinum')}`` Per Trade: ``{order.get('quantity')}`` Rank: ``{order.get('rank')}`` Visible = {order.get('visible')}\r\n\r\n"
            embed.set_footer(text="Data found by warframe.market")
            await interaction.followup.send(embed=embed)
            log.info("Found all data related to orders")
        except SellerError as exc:
            await interaction.followup.send(f"Couldn't find listings: {exc}", ephemeral=True)
            return
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

    @list_order.on_autocomplete("item")
    async def _list_ac(self, interaction: nextcord.Interaction, current: str):
        await self._suggest(interaction, current)
    @ducats_compare.on_autocomplete("item")
    async def _ducats_compare_ac(self, interaction: nextcord.Interaction, current: str):
        await self._suggest(interaction, current)
def setup(bot: commands.Bot) -> None:
    bot.add_cog(MarketCommands(bot))
