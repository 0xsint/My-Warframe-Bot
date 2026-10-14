"""/ability <name> -- rich ability page (stats, Info, Kit Interactions, Tips, Bugs + icon).

Load with:   bot.load_extension("cogs.ability_commands")

It is registered as a user-installable command (works in DMs / any server), the
same way the existing /wf group is. If your /wf cog already defines the
integration_types/contexts helpers, reuse those instead of the literals below.

To make the *existing* `/wf page` command show this view for ability pages,
see `send_ability_if_applicable` at the bottom.
"""
from __future__ import annotations

import aiohttp
import nextcord
from nextcord.ext import commands

from wiki.ability import fetch_ability, search_titles
from wiki.ability_view import AbilityView

USER_AND_GUILD = [nextcord.IntegrationType.guild_install, nextcord.IntegrationType.user_install]
EVERYWHERE = [
    nextcord.InteractionContextType.guild,
    nextcord.InteractionContextType.bot_dm,
    nextcord.InteractionContextType.private_channel,
]


class AbilityCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None

    @property
    def session(self) -> aiohttp.ClientSession:
        # Reuse bot.session if bot.py already created one, otherwise make our own.
        shared = getattr(self.bot, "session", None)
        if shared is not None and not shared.closed:
            return shared
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def cog_unload(self) -> None:
        if self._session and not self._session.closed:
            self.bot.loop.create_task(self._session.close())

    @nextcord.slash_command(
        name="ability",
        description="Look up a Warframe ability: stats, mechanics, kit interactions and tips.",
        integration_types=USER_AND_GUILD,
        contexts=EVERYWHERE,
    )
    async def ability(
        self,
        interaction: nextcord.Interaction,
        name: str = nextcord.SlashOption(description="Ability name, e.g. Nurinarim", autocomplete=True),
    ):
        await interaction.response.defer()
        try:
            page = await fetch_ability(self.session, name)
            if page is None:                                   # try fuzzy title match
                titles = await search_titles(self.session, name, 1)
                page = await fetch_ability(self.session, titles[0]) if titles else None
        except Exception as exc:                               # network / Cloudflare
            await interaction.followup.send(f"Couldn't reach the Warframe Wiki: {exc}")
            return

        if page is None:
            await interaction.followup.send(f"No wiki page found for **{name}**.")
            return
        if not page.is_ability:
            await interaction.followup.send(
                f"**{page.title}** isn't an ability page. Try `/wf page` for that one."
            )
            return

        view = AbilityView(page)
        view.message = await interaction.followup.send(embed=view.current_embed(), view=view)

    @ability.on_autocomplete("name")
    async def ability_autocomplete(self, interaction: nextcord.Interaction, current: str):
        if not current:
            await interaction.response.send_autocomplete([])
            return
        try:
            titles = await search_titles(self.session, current, 10)
        except Exception:
            titles = []
        await interaction.response.send_autocomplete([t[:100] for t in titles][:25])


async def send_ability_if_applicable(session, interaction: nextcord.Interaction, title: str) -> bool:
    """Drop-in for the existing `/wf page` command.

        # inside /wf page, after `await interaction.response.defer()`:
        if await send_ability_if_applicable(self.bot.session, interaction, name):
            return
        ... existing generic infobox embed code ...

    Returns True (and sends the rich view) when `title` is a Warframe ability page.
    """
    page = await fetch_ability(session, title)
    if page is None or not page.is_ability:
        return False
    view = AbilityView(page)
    view.message = await interaction.followup.send(embed=view.current_embed(), view=view)
    return True


def setup(bot: commands.Bot) -> None:
    bot.add_cog(AbilityCommands(bot))
