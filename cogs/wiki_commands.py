"""/wf -- look things up on the official WARFRAME Wiki (wiki.warframe.com).

    /wf warframe | weapon | mod | arcane | companion | enemy | relic | quest
                     typed lookups; autocomplete only suggests pages of that kind
    /wf ability      rich ability view (stats, Info, Kit Interactions, Tips, ...)
    /wf page         any wiki page
    /wf drops        open a page straight on its Acquisition / drop table section
    /wf compare      two pages' infobox stats side by side
    /wf search       search titles, then open a result from the dropdown
    /wf random       a random wiki article

Every command is registered for guild and user installs in every context, so
it works in servers, DMs and group DMs. Pages for tradable items get a link
button to warframe.market.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import nextcord
from nextcord import Interaction, SlashOption
from nextcord.ext import commands

from core.fuzzy import rank_by_name
from http_session import SessionProvider
from ui.paginator import view_kwargs
from wiki import PageNotFoundError, WikiError
from wiki.ability import fetch_ability, is_ability_title
from wiki.ability_view import AbilityView
from wiki.compare import build_compare_view
from wiki.embeds import build_error_embed, build_search_embed
from wiki.kinds import ABILITY_CATEGORIES, KINDS
from wiki.page_view import build_page_view, drops_section
from wiki.parser import WikiPage
from wiki.search_view import SearchView

log = logging.getLogger("warframebot.cogs.wiki")

USER_AND_GUILD = [nextcord.IntegrationType.guild_install, nextcord.IntegrationType.user_install]
EVERYWHERE = [
    nextcord.InteractionContextType.guild,
    nextcord.InteractionContextType.bot_dm,
    nextcord.InteractionContextType.private_channel,
]
AUTOCOMPLETE_LIMIT = 25
SEARCH_LIMIT = 50            # MediaWiki's opensearch maximum for non-bot clients
MARKET_LOOKUP_TIMEOUT = 3.0


class WikiCommands(commands.Cog):
    """``/wf`` command group backed by wiki.warframe.com."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Reuses the bot's session when present; otherwise owns one that closes with the bot.
        self.sessions = SessionProvider(bot)
        self._warming: set[str] = set()

    def cog_unload(self) -> None:
        self.bot.loop.create_task(self.sessions.close())

    # ------------------------------------------------------------ helpers
    async def _autocomplete(self, interaction: Interaction, current: str,
                            categories: tuple[str, ...] = ()) -> None:
        """Category-restricted suggestions when the category list is loaded,
        plain wiki title search otherwise (the list loads in the background)."""
        titles = self._loaded_titles(categories) if categories else None
        if titles:
            await interaction.response.send_autocomplete(rank_by_name(titles, current or "", AUTOCOMPLETE_LIMIT))
            return
        if categories:
            self._warm(categories)
        if not current:
            await interaction.response.send_autocomplete([])
            return
        try:
            results = await self.bot.wiki.search(current, limit=AUTOCOMPLETE_LIMIT)
        except WikiError:
            results = []
        await interaction.response.send_autocomplete([title[:100] for title, _ in results])

    def _loaded_titles(self, categories: tuple[str, ...]) -> list[str]:
        seen: dict[str, None] = {}
        for cat in categories:
            for title in self.bot.wiki.cached_category(cat) or ():
                seen.setdefault(title, None)
        return list(seen)

    def _warm(self, categories: tuple[str, ...]) -> None:
        key = "|".join(categories)
        if key in self._warming:
            return
        self._warming.add(key)

        async def load():
            try:
                await self.bot.wiki.categories_members(categories)
            except Exception:
                log.warning("Could not load wiki categories %s", categories, exc_info=True)
            finally:
                self._warming.discard(key)
        asyncio.create_task(load())

    async def _load_page(self, name: str) -> tuple[Optional[WikiPage], Optional[nextcord.Embed]]:
        """Fetch + parse a page. Returns ``(page, None)`` or ``(None, error_embed)``."""
        try:
            return await self.bot.wiki.fetch(name), None
        except PageNotFoundError as exc:
            return None, build_error_embed("Page not found", str(exc))
        except WikiError as exc:
            return None, build_error_embed("WARFRAME Wiki error", str(exc))
        except Exception:
            log.exception("Unexpected error looking up %r", name)
            return None, build_error_embed(
                "Something went wrong",
                "An unexpected error occurred while reading that page. Please try again later.")

    async def _market_links(self, title: str) -> list[tuple[str, str]]:
        """A warframe.market button for pages about tradable items."""
        market = getattr(self.bot, "market", None)
        if market is None:
            return []
        try:
            item = await asyncio.wait_for(market.find_exact(title), MARKET_LOOKUP_TIMEOUT)
        except Exception:
            return []
        return [("warframe.market", item.url)] if item else []

    async def _respond_with_page(self, interaction: Interaction, name: str, kind: Optional[str] = None,
                                 *, deferred: bool = False, start: Optional[str] = None,
                                 drops: bool = False) -> None:
        """Send a wiki page as an overview + dropdown + pagination view."""
        if not deferred:
            await interaction.response.defer()
        page, error = await self._load_page(name)
        if error is not None:
            await interaction.send(embed=error, ephemeral=True)
            return
        content = None
        if drops:
            start = drops_section(page)
            if start is None:
                content = f"**{page.title}** has no acquisition or drop section, so here is the full page."
        view = build_page_view(page, kind, links=await self._market_links(page.title), start=start)
        kwargs = view_kwargs(view)
        if content:
            kwargs["content"] = content
        view.message = await interaction.send(**kwargs)

    async def _open_from_search(self, interaction: Interaction, title: str) -> None:
        """A result was picked in /wf search: turn that same message into the page."""
        await interaction.response.defer()
        ability = await self._resolve_ability(title)
        if ability is not None:
            view = AbilityView(ability)
        else:
            page, error = await self._load_page(title)
            if error is not None:
                await interaction.followup.send(embed=error, ephemeral=True)
                return
            view = build_page_view(page, links=await self._market_links(page.title))
        view.message = await interaction.edit_original_message(
            embed=view.current_embed(), view=view if view.children else None)

    async def _resolve_ability(self, name: str):
        """A parsed AbilityPage for ``name`` (exact or fuzzy title), or None if
        it isn't a Warframe ability. Never raises: failure means "not handled"."""
        session = self.sessions.get()
        try:
            title = name
            is_ability = await is_ability_title(session, title)
            if is_ability is None:                       # no page under that exact title
                results = await self.bot.wiki.search(name, limit=1)
                if not results:
                    return None
                title = results[0][0]
                is_ability = await is_ability_title(session, title)
            if not is_ability:
                return None
            page = await fetch_ability(session, title)
        except Exception:
            log.exception("Ability lookup failed for %r", name)
            return None
        if page is None or not page.is_ability:
            return None
        if not (page.description or page.stats or page.sections):
            return None                                  # parsed nothing useful
        return page

    async def _send_ability(self, interaction: Interaction, page) -> None:
        view = AbilityView(page)
        view.message = await interaction.send(embed=view.current_embed(), view=view)

    # -------------------------------------------------------------- group
    @nextcord.slash_command(name="wf", description="Look things up on the WARFRAME Wiki",
                            integration_types=USER_AND_GUILD, contexts=EVERYWHERE, force_global=True)
    async def wf(self, interaction: Interaction) -> None:
        """Base command group; Discord only invokes registered subcommands."""

    # ---------------------------------------------------- typed lookups
    async def _typed(self, interaction: Interaction, kind: str, name: str) -> None:
        await self._respond_with_page(interaction, name, KINDS[kind].view_kind)

    @wf.subcommand(name="warframe", description="A Warframe's stats, passive and abilities")
    async def wf_warframe(self, interaction: Interaction,
                          name: str = SlashOption(description="Warframe, e.g. Excalibur", autocomplete=True)):
        await self._typed(interaction, "warframe", name)

    @wf.subcommand(name="weapon", description="A weapon's stats and attack modes")
    async def wf_weapon(self, interaction: Interaction,
                        name: str = SlashOption(description="Weapon, e.g. Braton Prime", autocomplete=True)):
        await self._typed(interaction, "weapon", name)

    @wf.subcommand(name="mod", description="A mod's effect per rank, polarity, drain and drop locations")
    async def wf_mod(self, interaction: Interaction,
                     name: str = SlashOption(description="Mod, e.g. Serration", autocomplete=True)):
        await self._typed(interaction, "mod", name)

    @wf.subcommand(name="arcane", description="An arcane enhancement's effect and where it drops")
    async def wf_arcane(self, interaction: Interaction,
                        name: str = SlashOption(description="Arcane, e.g. Arcane Energize", autocomplete=True)):
        await self._typed(interaction, "arcane", name)

    @wf.subcommand(name="companion", description="A sentinel, kubrow, kavat, MOA or hound")
    async def wf_companion(self, interaction: Interaction,
                           name: str = SlashOption(description="Companion, e.g. Smeeta Kavat", autocomplete=True)):
        await self._typed(interaction, "companion", name)

    @wf.subcommand(name="enemy", description="An enemy's health, armor, weaknesses and drops")
    async def wf_enemy(self, interaction: Interaction,
                       name: str = SlashOption(description="Enemy, e.g. Corrupted Heavy Gunner", autocomplete=True)):
        await self._typed(interaction, "enemy", name)

    @wf.subcommand(name="relic", description="A Void Relic's rewards and where to farm it")
    async def wf_relic(self, interaction: Interaction,
                       name: str = SlashOption(description="Relic, e.g. Axi A1", autocomplete=True)):
        await self._typed(interaction, "relic", name)

    @wf.subcommand(name="quest", description="A quest's requirements, rewards and walkthrough")
    async def wf_quest(self, interaction: Interaction,
                       name: str = SlashOption(description="Quest, e.g. The Second Dream", autocomplete=True)):
        await self._typed(interaction, "quest", name)

    # ------------------------------------------------------------ ability
    @wf.subcommand(name="ability", description="A Warframe ability: stats, mechanics, kit interactions and tips")
    async def wf_ability(self, interaction: Interaction,
                         name: str = SlashOption(description="Ability, e.g. Molecular Prime", autocomplete=True)):
        await interaction.response.defer()
        page = await self._resolve_ability(name)
        if page is None:
            await interaction.send(embed=build_error_embed(
                "Not an ability page",
                f"I couldn't find a Warframe ability called **{name}**. Try `/wf page` for other pages."),
                ephemeral=True)
            return
        await self._send_ability(interaction, page)

    # ------------------------------------------------------------ generic
    @wf.subcommand(name="page", description="Any WARFRAME Wiki page (mods, enemies, quests, factions, ...)")
    async def wf_page(self, interaction: Interaction,
                      name: str = SlashOption(description="Page name, e.g. Kuva Lich", autocomplete=True)):
        await interaction.response.defer()
        ability = await self._resolve_ability(name)       # ability pages get the rich view
        if ability is not None:
            await self._send_ability(interaction, ability)
            return
        await self._respond_with_page(interaction, name, deferred=True)

    @wf.subcommand(name="drops", description="Where to get something: opens its Acquisition / drop table section")
    async def wf_drops(self, interaction: Interaction,
                       name: str = SlashOption(description="Item, mod, relic, ..., e.g. Primed Flow",
                                               autocomplete=True)):
        await self._respond_with_page(interaction, name, drops=True)

    @wf.subcommand(name="compare", description="Compare two pages' stats side by side (weapons, Warframes, ...)")
    async def wf_compare(self, interaction: Interaction,
                         first: str = SlashOption(description="First page, e.g. Braton Prime", autocomplete=True),
                         second: str = SlashOption(description="Second page, e.g. Boltor Prime", autocomplete=True)):
        await interaction.response.defer()
        a, err = await self._load_page(first)
        if err is None:
            b, err = await self._load_page(second)
        if err is not None:
            await interaction.send(embed=err, ephemeral=True)
            return
        view = build_compare_view(a, b)
        view.message = await interaction.send(**view_kwargs(view))

    @wf.subcommand(name="search", description="Search the WARFRAME Wiki for matching pages")
    async def wf_search(self, interaction: Interaction,
                        query: str = SlashOption(description="What to search for")):
        await interaction.response.defer()
        try:
            results = await self.bot.wiki.search(query, limit=SEARCH_LIMIT)
        except WikiError as exc:
            await interaction.send(embed=build_error_embed("Search failed", str(exc)), ephemeral=True)
            return
        if not results:
            await interaction.send(embed=build_search_embed(query, results))
            return
        view = SearchView(query, results, self._open_from_search)
        view.message = await interaction.send(**view_kwargs(view))

    @wf.subcommand(name="random", description="Open a random WARFRAME Wiki article")
    async def wf_random(self, interaction: Interaction):
        await interaction.response.defer()
        try:
            title = await self.bot.wiki.random_title()
        except WikiError as exc:
            await interaction.send(embed=build_error_embed("WARFRAME Wiki error", str(exc)), ephemeral=True)
            return
        if not title:
            await interaction.send(embed=build_error_embed("Nothing found", "The wiki returned no page."),
                                   ephemeral=True)
            return
        await self._respond_with_page(interaction, title, deferred=True)

    # -------------------------------------------------------- autocomplete
    async def _ac_any(self, interaction: Interaction, current: str):
        await self._autocomplete(interaction, current)

    async def _ac_ability(self, interaction: Interaction, current: str):
        await self._autocomplete(interaction, current, ABILITY_CATEGORIES)

    wf_ability.on_autocomplete("name")(_ac_ability)
    wf_page.on_autocomplete("name")(_ac_any)
    wf_drops.on_autocomplete("name")(_ac_any)
    wf_compare.on_autocomplete("first")(_ac_any)
    wf_compare.on_autocomplete("second")(_ac_any)


def _typed_autocomplete(kind: str):
    async def callback(self: WikiCommands, interaction: Interaction, current: str):
        await self._autocomplete(interaction, current, KINDS[kind].categories)
    callback.__name__ = f"_ac_{kind}"
    return callback


for _kind, _cmd in (("warframe", WikiCommands.wf_warframe), ("weapon", WikiCommands.wf_weapon),
                    ("mod", WikiCommands.wf_mod), ("arcane", WikiCommands.wf_arcane),
                    ("companion", WikiCommands.wf_companion), ("enemy", WikiCommands.wf_enemy),
                    ("relic", WikiCommands.wf_relic), ("quest", WikiCommands.wf_quest)):
    _cmd.on_autocomplete("name")(_typed_autocomplete(_kind))
del _kind, _cmd


def setup(bot: commands.Bot) -> None:
    bot.add_cog(WikiCommands(bot))
