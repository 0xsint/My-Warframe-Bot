"""/wm  -- warframe.market tools aimed at people who SELL.

    /wm price   item:<..> [rank] [platform] [my_price]   pricing dashboard
    /wm buyers  item:<..> [rank] [platform]              online buyers + copy-paste whispers
    /wm sellers item:<..> [rank] [platform]              your online competition
    /wm riven   weapon:<..> [stat1..3] [negative] [polarity] ...   riven contracts (auctions) for a weapon
    /wm lich    weapon:<..> [element] [min_damage] ...   Kuva Lich auctions
    /wm sister  weapon:<..> [element] [min_damage] ...   Sister of Parvos auctions
    /wm link / unlink                                    store / delete your warframe.market login
    /wm list    item:<..> price:<..> [...]               post a listing   (allow-listed users)
    /wm unlist  order_id:<..>                            remove a listing (allow-listed users)

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
from market.embeds import (
    MARKET_FOOTER,
    ducats_embed,
    listed_embed,
    my_orders_sections,
    order_pages,
    removed_embed,
    report_sections,
    lich_pages,
    riven_pages,
    trend_pages,
    updated_embed,
)
from market.riven import (ELEMENTS, POLARITIES, LichFilter, RivenFilter, filter_liches,
                          filter_rivens, stat_suggestions, unknown_stats)
from market.seller import MarketSeller, SellerError
from market.store import AllowList, StoreError, UserStore
from ui.paginator import Section, SectionView, view_kwargs

log = logging.getLogger("warframebot.cogs.market")

USER_AND_GUILD = [nextcord.IntegrationType.guild_install, nextcord.IntegrationType.user_install]
EVERYWHERE = [
    nextcord.InteractionContextType.guild,
    nextcord.InteractionContextType.bot_dm,
    nextcord.InteractionContextType.private_channel,
]
PLATFORM_CHOICES = {"PC": "pc", "PlayStation": "ps4", "Xbox": "xbox", "Switch": "switch"}
ORDER_TYPE_CHOICES = {"Sell": "sell", "Buy": "buy"}
ELEMENT_CHOICES = {e.title(): e for e in ELEMENTS}
RIVEN_POLARITY_CHOICES = {p.title(): p for p in POLARITIES}


def _item_option():
    return nextcord.SlashOption(description="Item name, e.g. Mirage Prime Set", autocomplete=True)


def _rank_option():
    return nextcord.SlashOption(description="Mod/arcane rank (0 = unranked, omit for all)",
                                required=False, min_value=0, max_value=10)


def _platform_option():
    return nextcord.SlashOption(description="Platform (default PC)", required=False,
                                choices=PLATFORM_CHOICES, default="pc")


def _is_owner(interaction: nextcord.Interaction) -> bool:
    """Owners (OWNER_ID in .env, comma-separated Discord ids) are always allowed
    to sell and may use the shared WFM_* account from .env."""
    raw = os.getenv("OWNER_ID", "")
    allowed = {int(x) for x in raw.replace(" ", "").split(",") if x.isdigit()}
    return interaction.user.id in allowed


LINK_NOTICE = ("Your email and password are encrypted before they are stored, and only used "
               "to sign in to warframe.market. Discord shows what you type here to you only.")


class LinkModal(nextcord.ui.Modal):
    """Asks for warframe.market credentials in a private form, so they never
    appear as slash-command arguments or in the channel."""

    def __init__(self, cog: "MarketCommands") -> None:
        super().__init__("Link warframe.market account")
        self.cog = cog
        self.email = nextcord.ui.TextInput(
            label="Email (stored encrypted)", placeholder="you@example.com",
            max_length=254, required=True)
        self.password = nextcord.ui.TextInput(
            label="Password (stored encrypted)", placeholder="Encrypted before it is saved",
            max_length=128, required=True)
        self.notice = nextcord.ui.TextInput(
            label="Read me", style=nextcord.TextInputStyle.paragraph, required=False,
            default_value=LINK_NOTICE + " Leave this box as it is.", max_length=400)
        self.add_item(self.email)
        self.add_item(self.password)
        self.add_item(self.notice)

    async def callback(self, interaction: nextcord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.cog._finish_link(interaction, (self.email.value or "").strip(), self.password.value or "")


class MarketCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self._client: WarframeMarketClient | None = None
        self._seller: MarketSeller | None = None
        self._store: UserStore | None = None
        self._user_sellers: dict[int, MarketSeller] = {}
        self._allow = AllowList()

    @property
    def store(self) -> UserStore:
        if self._store is None:
            self._store = UserStore.from_env()
        return self._store

    def _may_sell(self, interaction: nextcord.Interaction) -> bool:
        return _is_owner(interaction) or self._allow.is_allowed(interaction.user.id)

    async def _seller_for(self, interaction: nextcord.Interaction) -> MarketSeller | None:
        """The caller's own warframe.market session, or None after telling them why not.
        Call after interaction.response.defer(ephemeral=True)."""
        uid = interaction.user.id
        if not self._may_sell(interaction):
            await interaction.followup.send(
                "You're not on the list of users allowed to sell. Ask the bot owner to add your Discord id.",
                ephemeral=True)
            return None
        cached = self._user_sellers.get(uid)
        if cached is not None:
            return cached
        try:
            creds = await self.store.get(uid)
        except StoreError as exc:
            log.error("Credential store problem: %s", exc)
            await interaction.followup.send("Account storage isn't available right now.", ephemeral=True)
            return None
        except Exception:
            log.exception("Could not read credentials from MongoDB")
            await interaction.followup.send("Account storage isn't available right now.", ephemeral=True)
            return None
        if creds is None:
            if _is_owner(interaction) and self.seller.configured:
                return self.seller
            await interaction.followup.send("Link your warframe.market account first with `/wm link`.",
                                            ephemeral=True)
            return None
        seller = MarketSeller(creds.email, creds.password, jwt=creds.jwt,
                              on_token=lambda token, uid=uid: self.store.save_token(uid, token))
        self._user_sellers[uid] = seller
        return seller

    async def _finish_link(self, interaction: nextcord.Interaction, email: str, password: str) -> None:
        if not self._may_sell(interaction):
            await interaction.followup.send("You're not on the list of users allowed to sell.", ephemeral=True)
            return
        if not email or not password:
            await interaction.followup.send("Email and password are both required.", ephemeral=True)
            return
        uid = interaction.user.id
        seller = MarketSeller(email, password, on_token=lambda token: self.store.save_token(uid, token))
        seller.
        try:
            token = await seller.login()    # only save credentials that actually work
            await self.store.save(uid, email, password, jwt=token)
        except SellerError as exc:
            await seller.close()
            await interaction.followup.send(f"Couldn't sign in to warframe.market: {exc}", ephemeral=True)
            return
        except StoreError as exc:
            await seller.close()
            log.error("Credential store problem: %s", exc)
            await interaction.followup.send("Account storage isn't available right now.", ephemeral=True)
            return
        except Exception:
            await seller.close()
            log.exception("Could not link account")
            await interaction.followup.send("Couldn't save your account - check the log.", ephemeral=True)
            return
        old = self._user_sellers.pop(interaction.user.id, None)
        if old is not None:
            await old.close()
        self._user_sellers[interaction.user.id] = seller
        await interaction.followup.send(
            "Linked. Your email, password and login cookie are stored encrypted, and the cookie is "
            "used from now on; your password is only used to sign in again if the cookie stops working. You can now use `/wm list`, `/wm unlist`, "
            "`/wm update_order` and `/wm my_orders`. Use `/wm unlink` to delete them.", ephemeral=True)

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
        for seller in self._user_sellers.values():
            self.bot.loop.create_task(seller.close())

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
        report = build_report(it.name, it.slug, orders, rank=rank, history=history, ladder_size=12)
        view = SectionView(report_sections(report, it, platform=platform, my_price=my_price),
                           footer_text="Online/in-game players only \u00B7 " + MARKET_FOOTER, chain=True,
                           select_placeholder="Jump to a section")
        view.message = await interaction.followup.send(**view_kwargs(view))

    async def _send_orders(self, interaction, item_q: str, rank, platform: str, side: str) -> None:
        got = await self._fetch(interaction, item_q, platform)
        if not got:
            return
        it, orders = got
        view = SectionView([Section("Orders", order_pages(it, orders, side=side, rank=rank))],
                           footer_text="Whispers use warframe.market's standard format \u00B7 " + MARKET_FOOTER)
        view.message = await interaction.followup.send(**view_kwargs(view))

    @wm.subcommand(name="buyers", description="Online buyers you can sell to right now (with whispers)")
    async def buyers(self, interaction: nextcord.Interaction,
                     item: str = _item_option(), rank: int = _rank_option(),
                     platform: str = _platform_option()):
        await interaction.response.defer()
        await self._send_orders(interaction, item, rank, platform, "buy")

    @wm.subcommand(name="sellers", description="Cheapest online sellers (your competition)")
    async def sellers(self, interaction: nextcord.Interaction,
                      item: str = _item_option(), rank: int = _rank_option(),
                      platform: str = _platform_option()):
        await interaction.response.defer()
        await self._send_orders(interaction, item, rank, platform, "sell")

    # ------------------------------------------------------- riven contracts
    @wm.subcommand(name="riven", description="Riven mod contracts for a weapon, filtered by stats")
    async def riven(self, interaction: nextcord.Interaction,
                    weapon: str = nextcord.SlashOption(description="Weapon name, e.g. Acceltra",
                                                       autocomplete=True),
                    stat1: str = nextcord.SlashOption(description="Wanted positive stat, e.g. critical damage",
                                                      required=False, autocomplete=True),
                    stat2: str = nextcord.SlashOption(description="Second wanted positive stat",
                                                      required=False, autocomplete=True),
                    stat3: str = nextcord.SlashOption(description="Third wanted positive stat",
                                                      required=False, autocomplete=True),
                    negative: str = nextcord.SlashOption(description="Only rivens whose negative stat is this",
                                                         required=False, autocomplete=True),
                    polarity: str = nextcord.SlashOption(description="Riven polarity", required=False,
                                                         choices=RIVEN_POLARITY_CHOICES),
                    rank: int = nextcord.SlashOption(description="Mod rank 0-8 (omit for all)",
                                                     required=False, min_value=0, max_value=8),
                    max_price: int = nextcord.SlashOption(description="Highest buyout or bid to show",
                                                          required=False, min_value=1),
                    online_only: bool = nextcord.SlashOption(description="Only sellers who are online (default yes)",
                                                             required=False, default=True),
                    platform: str = _platform_option()):
        await interaction.response.defer()
        try:
            weapons = await self.client.resolve_riven_weapons(weapon)
            auctions = []
            for it in weapons:
                auctions += await self.client.get_riven_auctions(it.slug, platform=platform)
        except ItemNotFound:
            await interaction.followup.send(f"No riven weapon matches **{weapon}**.")
            return
        except MarketError as exc:
            await interaction.followup.send(f"warframe.market problem: {exc}")
            return

        family = len(weapons) > 1
        label = f"{weapon.strip().title()} weapons" if family else weapons[0].name
        wanted = tuple(s.strip() for s in (stat1, stat2, stat3) if s and s.strip())
        neg = (negative or "").strip() or None
        bad = unknown_stats(auctions, [*wanted, *([neg] if neg else [])])
        if bad:
            await interaction.followup.send(
                f"No riven for **{label}** has a stat matching: {', '.join(bad)}. "
                "Try a shorter word, e.g. `crit`, `multishot`, `toxin`.")
            return

        if not auctions:
            await interaction.followup.send(f"No riven contracts listed for **{label}** on {platform.upper()}.")
            return
        matches = filter_rivens(auctions, RivenFilter(polarity=polarity, rank=rank, positives=wanted,
                                                      negative=neg, max_price=max_price,
                                                      online_only=online_only))
        names = {it.slug: it.name for it in weapons} if family else None
        pages = riven_pages(weapons[0], matches, label=label, weapon_names=names)
        view = SectionView([Section("Rivens", pages)],
                           footer_text="Cheapest first \u00B7 " + MARKET_FOOTER)
        view.message = await interaction.followup.send(**view_kwargs(view))

    # ------------------------------------------------ account link / unlink
    @wm.subcommand(name="link", description="Link your warframe.market account so you can list and unlist")
    async def link(self, interaction: nextcord.Interaction):
        if not self._may_sell(interaction):
            await interaction.response.send_message(
                "You're not on the list of users allowed to sell. Ask the bot owner to add your Discord id.",
                ephemeral=True)
            return
        await interaction.response.send_modal(LinkModal(self))

    @wm.subcommand(name="unlink", description="Delete your stored warframe.market credentials")
    async def unlink(self, interaction: nextcord.Interaction):
        await interaction.response.defer(ephemeral=True)
        seller = self._user_sellers.pop(interaction.user.id, None)
        if seller is not None:
            await seller.close()
        try:
            removed = await self.store.delete(interaction.user.id)
        except Exception:
            log.exception("Could not delete credentials")
            await interaction.followup.send("Account storage isn't available right now.", ephemeral=True)
            return
        await interaction.followup.send("Your stored credentials were deleted." if removed
                                        else "You had no linked account.", ephemeral=True)

    # ------------------------------------------ Kuva Lich / Sister auctions
    async def _lich_auctions(self, interaction: nextcord.Interaction, kind: str, weapon: str,
                             element, min_damage, ephemera, max_price, online_only, platform):
        await interaction.response.defer()
        try:
            item = await self.client.resolve_lich_weapon(kind, weapon)
            auctions = await self.client.get_lich_auctions(kind, item.slug, platform=platform)
        except ItemNotFound:
            await interaction.followup.send(f"No {'Kuva Lich' if kind == 'lich' else 'Sister of Parvos'} "
                                            f"weapon matches **{weapon}**.")
            return
        except MarketError as exc:
            await interaction.followup.send(f"warframe.market problem: {exc}")
            return
        if not auctions:
            await interaction.followup.send(f"No auctions listed for **{item.name}** on {platform.upper()}.")
            return
        matches = filter_liches(auctions, LichFilter(element=element, min_damage=min_damage,
                                                     ephemera=ephemera, max_price=max_price,
                                                     online_only=online_only))
        view = SectionView([Section("Auctions", lich_pages(kind, item, matches))],
                           footer_text="Cheapest first \u00B7 " + MARKET_FOOTER)
        view.message = await interaction.followup.send(**view_kwargs(view))

    @wm.subcommand(name="lich", description="Kuva Lich weapon auctions")
    async def lich(self, interaction: nextcord.Interaction,
                   weapon: str = nextcord.SlashOption(description="Kuva weapon, e.g. Kuva Kohm", autocomplete=True),
                   element: str = nextcord.SlashOption(description="Bonus element", required=False,
                                                       choices=ELEMENT_CHOICES),
                   min_damage: int = nextcord.SlashOption(description="Minimum bonus damage %",
                                                          required=False, min_value=25, max_value=60),
                   ephemera: bool = nextcord.SlashOption(description="Has an ephemera (omit for any)", required=False),
                   max_price: int = nextcord.SlashOption(description="Highest buyout or bid to show",
                                                         required=False, min_value=1),
                   online_only: bool = nextcord.SlashOption(description="Only sellers who are online (default yes)",
                                                            required=False, default=True),
                   platform: str = _platform_option()):
        await self._lich_auctions(interaction, "lich", weapon, element, min_damage, ephemera,
                                  max_price, online_only, platform)

    @wm.subcommand(name="sister", description="Sister of Parvos (Tenet/Coda) weapon auctions")
    async def sister(self, interaction: nextcord.Interaction,
                     weapon: str = nextcord.SlashOption(description="Tenet/Coda weapon, e.g. Tenet Cycron",
                                                        autocomplete=True),
                     element: str = nextcord.SlashOption(description="Bonus element", required=False,
                                                         choices=ELEMENT_CHOICES),
                     min_damage: int = nextcord.SlashOption(description="Minimum bonus damage %",
                                                            required=False, min_value=25, max_value=60),
                     ephemera: bool = nextcord.SlashOption(description="Has an ephemera (omit for any)", required=False),
                     max_price: int = nextcord.SlashOption(description="Highest buyout or bid to show",
                                                           required=False, min_value=1),
                     online_only: bool = nextcord.SlashOption(description="Only sellers who are online (default yes)",
                                                              required=False, default=True),
                     platform: str = _platform_option()):
        await self._lich_auctions(interaction, "sister", weapon, element, min_damage, ephemera,
                                  max_price, online_only, platform)

    async def _lich_suggest(self, interaction: nextcord.Interaction, kind: str, current: str):
        try:
            weapons = await self.client.search_lich_weapons(kind, current, 25)
        except Exception:
            weapons = []
        await interaction.response.send_autocomplete({w.name[:100]: w.name[:100] for w in weapons})

    @lich.on_autocomplete("weapon")
    async def _lich_ac(self, interaction: nextcord.Interaction, current: str):
        await self._lich_suggest(interaction, "lich", current)

    @sister.on_autocomplete("weapon")
    async def _sister_ac(self, interaction: nextcord.Interaction, current: str):
        await self._lich_suggest(interaction, "sister", current)

    # ------------------------------------------------- posting 
    @wm.subcommand(name="list", description="Post a listing on your warframe.market account")
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
        await interaction.response.defer(ephemeral=True)
        seller = await self._seller_for(interaction)
        if seller is None:
            return
        try:
            it = await self.client.resolve_item(item) # Resolve the item to get its slug and other details
            order = await seller.create_order(
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
        await interaction.followup.send(embed=listed_embed(order), ephemeral=True)
    @wm.subcommand(name="ducats_compare", description="Compare item prices in ducats and platinum")
    async def ducats_compare(self, interaction: nextcord.Interaction, item: str = _item_option()):
        await interaction.response.defer()          # this makes several API calls
        try:
            data = await self.seller.compare_ducats(item)
            try:
                it = await self.client.resolve_item(item)    # only for the picture/link
            except MarketError:
                it = None
        except (SellerError, MarketError) as exc:
            await interaction.followup.send(f"Couldn't compare that item: {exc}", ephemeral=True)
            return
        except Exception:
            log.exception("Unexpected error in ducats_compare")
            await interaction.followup.send("Unexpected error - check the log.", ephemeral=True)
            return
        await interaction.followup.send(embed=ducats_embed(data, it))

    @wm.subcommand(name="trends", description="Analyze recent market trends based on recent orders")
    async def trends(self, interaction: nextcord.Interaction):
        await interaction.response.defer()
        await interaction.followup.send("Analyzing recent orders...This may take a while..", ephemeral=True)
        try:
            trends = await self.seller.analyze_recent_orders()
            log.info("Analyzed recent orders and generated trends.")
        except SellerError as se:
            await interaction.followup.send(f"An error occurred while analyzing trends: {se}", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f"An unexpected error occurred: {e}", ephemeral=True)
            log.error(f"An unexpected error occurred: {e}")
            return
        view = SectionView([Section("Trends", trend_pages(trends))],
                           footer_text="Based on the latest orders \u00B7 " + MARKET_FOOTER)
        view.message = await interaction.followup.send(**view_kwargs(view), ephemeral=False)

    @wm.subcommand(name="update_order", description="Update one of your listings by order id ")
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
        await interaction.response.defer(ephemeral=True)
        seller = await self._seller_for(interaction)
        if seller is None:
            return
        try:
            updated = await seller.update_order(
                order_id.strip(), order_type=order_type, quantity=quantity, platinum=platinum,
                platform=platform, perTrade=per_item, visible=visible, charges=charges,
                rank=rank, ambers=ambers, cyans=cyans)
        except SellerError as exc:
            await interaction.followup.send(f"Couldn't update that listing: {exc}", ephemeral=True)
            return
        except Exception:
            log.exception("Unexpected error while updating a listing")
            await interaction.followup.send("Unexpected error while updating - check the log.", ephemeral=True)
            return
        # fields the user actually passed (order_type is required, so it isn't a "change")
        asked = {"quantity": quantity, "platinum": platinum, "perTrade": per_item, "visible": visible,
                 "charges": charges, "rank": rank, "amberStars": ambers, "cyanStars": cyans}
        changed = frozenset(k for k, v in asked.items() if v is not None)
        await interaction.followup.send(embed=updated_embed(updated, changed), ephemeral=True)
    @wm.subcommand(name="unlist", description="Remove one of your listings by order id ")
    async def unlist_order(self, interaction: nextcord.Interaction,
                           order_id: str = nextcord.SlashOption(description="Order id shown when you listed it"),
                           platform: str = _platform_option()):
        await interaction.response.defer(ephemeral=True)
        seller = await self._seller_for(interaction)
        if seller is None:
            return
        try:
            await seller.delete_order(order_id.strip(), platform=platform) # Attempt to delete the order with the specified ID
        except SellerError as exc:
            await interaction.followup.send(f"Couldn't remove that listing: {exc}", ephemeral=True)
            return
        await interaction.followup.send(embed=removed_embed(order_id.strip()), ephemeral=True)
    @wm.subcommand(name="my_orders", description="List all of your current orders")
    async def my_orders(self, interaction: nextcord.Interaction):
        await interaction.response.defer(ephemeral=True)
        seller = await self._seller_for(interaction)
        if seller is None:
            return
        try:
            orders = await seller.get_my_orders()
        except SellerError as exc:
            await interaction.followup.send(f"Couldn't find listings: {exc}", ephemeral=True)
            return
        view = SectionView(my_orders_sections(orders), footer_text="Data found by warframe.market",
                           select_placeholder="Filter orders")
        view.message = await interaction.followup.send(**view_kwargs(view), ephemeral=True)
        log.info("Found all data related to orders")
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

    @riven.on_autocomplete("weapon")
    async def _riven_ac(self, interaction: nextcord.Interaction, current: str):
        try:
            weapons = await self.client.search_riven_weapons(current, 25)
        except Exception:
            weapons = []
        await interaction.response.send_autocomplete({w.name[:100]: w.name[:100] for w in weapons})

    async def _stat_suggest(self, interaction: nextcord.Interaction, current: str) -> None:
        try:
            slugs = await self.client.get_riven_attributes()
        except Exception:
            slugs = []
        names = stat_suggestions(current, slugs, 25)
        await interaction.response.send_autocomplete({n[:100]: n[:100] for n in names})

    @riven.on_autocomplete("stat1")
    async def _riven_stat1_ac(self, interaction: nextcord.Interaction, current: str):
        await self._stat_suggest(interaction, current)

    @riven.on_autocomplete("stat2")
    async def _riven_stat2_ac(self, interaction: nextcord.Interaction, current: str):
        await self._stat_suggest(interaction, current)

    @riven.on_autocomplete("stat3")
    async def _riven_stat3_ac(self, interaction: nextcord.Interaction, current: str):
        await self._stat_suggest(interaction, current)

    @riven.on_autocomplete("negative")
    async def _riven_negative_ac(self, interaction: nextcord.Interaction, current: str):
        await self._stat_suggest(interaction, current)

def setup(bot: commands.Bot) -> None:
    bot.add_cog(MarketCommands(bot))
