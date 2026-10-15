"""Slash commands for looking things up on the WARFRAME Wiki.

Every command here is registered with both ``guild_install`` and
``user_install`` integration types and all interaction contexts, which is
what makes them usable as Discord **User Install** (a.k.a. "user apps")
commands - meaning anyone can add this bot to their own Discord account and
run ``/wf ...`` in any server, DM, or group DM, even ones the bot itself was
never added to.
"""

from __future__ import annotations

import logging
from typing import Callable

import nextcord
from nextcord import Interaction, IntegrationType, InteractionContextType, SlashOption
from nextcord.ext import commands

from http_session import SessionProvider
from wiki import PageNotFoundError, WikiError
from wiki.ability import fetch_ability, is_ability_title
from wiki.ability_view import AbilityView
from wiki.embeds import (
    build_error_embed,
    build_generic_embed,
    build_search_embed,
    build_warframe_embed,
    build_weapon_embed,
)
from wiki.parser import WikiPage

log = logging.getLogger("warframebot.cogs.wiki")

_INTEGRATION_TYPES = [0, 1]
_CONTEXTS = [
  0,
  1,
  2,
]

AUTOCOMPLETE_LIMIT = 10


class WikiCommands(commands.Cog):
    """``/wf`` command group backed by wiki.warframe.com."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Reuses bot.session / the wiki client's session when present; otherwise owns
        # one and closes it when the bot closes (no "unclosed client session" warning).
        self.sessions = SessionProvider(bot)

    def cog_unload(self) -> None:
        self.bot.loop.create_task(self.sessions.close())

    def _http_session(self):
        return self.sessions.get()

    async def _autocomplete(self, interaction: Interaction, current: str) -> None:
        if not current:
            await interaction.response.send_autocomplete([])
            return
        try:
            print(f"Autocomplete search for: {current}")
            results = await self.bot.wiki.search(current, limit=AUTOCOMPLETE_LIMIT)
            print(f"Autocomplete results for {current}: {results}")
        except WikiError:
            await interaction.response.send_autocomplete([])
            return
        await interaction.response.send_autocomplete([title for title, _ in results])

    async def _respond_with_page(
        self,
        interaction: Interaction,
        name: str,
        builder: Callable[[WikiPage], nextcord.Embed],
        *,
        deferred: bool = False,
    ) -> None:
        if not deferred:
            await interaction.response.defer()
        try:
            page = await self.bot.wiki.fetch(name)
        except PageNotFoundError as exc:
            await interaction.send(embed=build_error_embed("Page not found", str(exc)), ephemeral=True)
            return
        except WikiError as exc:
            await interaction.send(embed=build_error_embed("WARFRAME Wiki error", str(exc)), ephemeral=True)
            return
        except Exception:
            # log.exception already records the full traceback. To also keep it in
            # log.log, attach a (rotating) FileHandler to the "warframebot"
            # logger in bot.py instead of opening the file by hand here.
            log.exception("Unexpected error looking up %r", name) # Log the full traceback for debugging purposes
            await interaction.send(
                embed=build_error_embed(
                    "Something went wrong",
                    "An unexpected error occurred while reading that page. Please try again later.",
                ),
                ephemeral=True,
            )
            return
        await interaction.send(embed=builder(page))

    # -- ability pages ------------------------------------------------------

    async def _resolve_ability(self, name: str):
        """Return a parsed AbilityPage for ``name`` (exact or fuzzy title), or None
        if the page isn't a Warframe ability. Never raises: any failure just
        means "not handled", so callers fall back to the generic embed."""
        session = self._http_session()
        try:
            title = name
            is_ability = await is_ability_title(session, title)
            if is_ability is None:  # page doesn't exist under that exact title
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
            return None  # parsed nothing useful; let the generic embed handle it
        return page

    async def _send_ability(self, interaction: Interaction, page) -> None:
        view = AbilityView(page)
        view.message = await interaction.send(embed=view.current_embed(), view=view)

    # -- command group ----------------------------------------------------

    @nextcord.slash_command(
        name="wf",
        description="Look things up on the WARFRAME Wiki",
        integration_types=_INTEGRATION_TYPES,
        contexts=_CONTEXTS,
        force_global=True,
    )
    async def wf(self, interaction: Interaction) -> None:
        """Base command group; Discord only invokes registered subcommands."""

    @wf.subcommand(name="warframe", description="Look up a Warframe's stats, passive, and abilities [BETA]")
    async def wf_warframe(
        self,
        interaction: Interaction,
        name: str = SlashOption(description="Warframe name, e.g. Excalibur", autocomplete=True),
    ) -> None:
        await self._respond_with_page(interaction, name, build_warframe_embed)

    @wf_warframe.on_autocomplete("name")
    async def wf_warframe_autocomplete(self, interaction: Interaction, current: str) -> None:
        print(current)
        await self._autocomplete(interaction, current)

    @wf.subcommand(
        name="ability",
        description="Look up a Warframe ability: stats, mechanics, kit interactions and tips",
    )
    async def wf_ability(
        self,
        interaction: Interaction,
        name: str = SlashOption(description="Ability name, e.g. Nurinarim or Molecular Prime", autocomplete=True),
    ) -> None:
        await interaction.response.defer()
        page = await self._resolve_ability(name)
        if page is None:
            await interaction.send(
                embed=build_error_embed(
                    "Not an ability page",
                    f"I couldn't find a Warframe ability called **{name}**. "
                    "Try `/wf page` for other wiki pages.",
                ),
                ephemeral=True,
            )
            return
        await self._send_ability(interaction, page)

    @wf_ability.on_autocomplete("name")
    async def wf_ability_autocomplete(self, interaction: Interaction, current: str) -> None:
        await self._autocomplete(interaction, current)

    @wf.subcommand(name="weapon", description="Look up a weapon's stats")
    async def wf_weapon(
        self,
        interaction: Interaction,
        name: str = SlashOption(description="Weapon name, e.g. Braton", autocomplete=True),
    ) -> None:
        await self._respond_with_page(interaction, name, build_weapon_embed)

    @wf_weapon.on_autocomplete("name")
    async def wf_weapon_autocomplete(self, interaction: Interaction, current: str) -> None:
        await self._autocomplete(interaction, current)

    @wf.subcommand(
        name="page",
        description="Look up any WARFRAME Wiki page (mods, enemies, relics, quests, abilities, ...)",
    )
    async def wf_page(
        self,
        interaction: Interaction,
        name: str = SlashOption(description="Page name, e.g. Serration or Kuva Lich", autocomplete=True),
    ) -> None:
        await interaction.response.defer()
        # Ability pages get the rich view (stats, Info, Kit Interactions, Tips, icon);
        # everything else - or any failure - falls through to the generic embed.
        ability = await self._resolve_ability(name)
        if ability is not None:
            await self._send_ability(interaction, ability)
            return
        await self._respond_with_page(interaction, name, build_generic_embed, deferred=True)

    @wf_page.on_autocomplete("name")
    async def wf_page_autocomplete(self, interaction: Interaction, current: str) -> None:
        await self._autocomplete(interaction, current)

    @wf.subcommand(name="search", description="Search the WARFRAME Wiki for matching pages")
    async def wf_search(
        self,
        interaction: Interaction,
        query: str = SlashOption(description="What to search for"),
    ) -> None:
        await interaction.response.defer()
        try:
            results = await self.bot.wiki.search(query, limit=10)
        except WikiError as exc:
            await interaction.send(embed=build_error_embed("Search failed", str(exc)), ephemeral=True)
            return
        await interaction.send(embed=build_search_embed(query, results))


def setup(bot: commands.Bot) -> None:
    bot.add_cog(WikiCommands(bot))
