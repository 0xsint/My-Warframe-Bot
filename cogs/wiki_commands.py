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

from wiki import PageNotFoundError, WikiError
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

    async def _autocomplete(self, interaction: Interaction, current: str) -> None:
        if not current:
            await interaction.response.send_autocomplete([])
            return
        try:
            results = await self.bot.wiki.search(current, limit=AUTOCOMPLETE_LIMIT)
        except WikiError:
            await interaction.response.send_autocomplete([])
            return
        await interaction.response.send_autocomplete([title for title, _ in results])

    async def _respond_with_page(
        self,
        interaction: Interaction,
        name: str,
        builder: Callable[[WikiPage], nextcord.Embed],
    ) -> None:
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
            log.exception("Unexpected error looking up %r", name)
            fp = open("error.log", "w")
            fp.write(f"Unexpected error looking up {name}\n")
            fp.close()
            print(f"Unexpected error looking up {name}")
            import traceback
            traceback.print_exc()
            fp = open("error.log", "a")
            traceback.print_exc(file=fp)
            fp.close()
            print(f"Unexpected error looking up {name}, see error.log for details.")
            await interaction.send(
                embed=build_error_embed(
                    "Something went wrong",
                    "An unexpected error occurred while reading that page. Please try again later.",
                ),
                ephemeral=True,
            )
            return
        await interaction.send(embed=builder(page))

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

    @wf.subcommand(name="warframe", description="Look up a Warframe's stats, passive, and abilities")
    async def wf_warframe(
        self,
        interaction: Interaction,
        name: str = SlashOption(description="Warframe name, e.g. Excalibur", autocomplete=True),
    ) -> None:
        await self._respond_with_page(interaction, name, build_warframe_embed)

    @wf_warframe.on_autocomplete("name")
    async def wf_warframe_autocomplete(self, interaction: Interaction, current: str) -> None:
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
        description="Look up any WARFRAME Wiki page (mods, enemies, relics, quests, ...)",
    )
    async def wf_page(
        self,
        interaction: Interaction,
        name: str = SlashOption(description="Page name, e.g. Serration or Kuva Lich", autocomplete=True),
    ) -> None:
        await self._respond_with_page(interaction, name, build_generic_embed)

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
