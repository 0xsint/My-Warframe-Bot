"""Entry point for WarframeBOT.

A Discord **user-installable app** (works in servers it's added to, in DMs,
and in group DMs for anyone who installs it to their own account) that looks
up information from the official WARFRAME Wiki (https://wiki.warframe.com).
"""

from __future__ import annotations

import logging
import os
import sys
import asyncio
import aiohttp
import nextcord
from dotenv import load_dotenv
from nextcord.ext import commands

from wiki.client import USER_AGENT, WarframeWikiClient

load_dotenv()

logging.basicConfig(
    filename="log.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
log = logging.getLogger("warframebot")
TOKEN = os.getenv("DISCORD_TOKEN")

intents = nextcord.Intents.default()
intents.message_content = False
intents.members = False
intents.presences = False
# This bot never reads message content or needs member/presence data - it
# only responds to slash command interactions. No privileged intents need
# to be enabled in the Discord Developer Portal.


from market.client import WarframeMarketClient   # new import

class WarframeBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(intents=intents)
        self.wiki = None
        self.market: WarframeMarketClient | None = None   # new
        self._http_session = None
        self.load_extension("cogs.wiki_commands")
        self.load_extension("cogs.market_commands")       # new

    async def start(self, token, *, reconnect=True):
        self._http_session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
        self.wiki = WarframeWikiClient(self._http_session)
        self.market = WarframeMarketClient(self._http_session)          # new
        asyncio.create_task(self.market._load_items())  # new: warm autocomplete (add `import asyncio`)
        await super().start(token, reconnect=reconnect)
    async def close(self) -> None:
        if self._http_session is not None and not self._http_session.closed:
            await self._http_session.close()
        await super().close()


def build_invite_url(application_id: int) -> str:
    """Invite link that lets a user add this app to their own account
    (user install) in addition to the normal server-install flow."""
    return (
        "https://discord.com/oauth2/authorize"
        f"?client_id={application_id}&integration_type=1&scope=applications.commands"
    )


def main() -> None:
    if not TOKEN:
        log.error(
            "DISCORD_TOKEN is not set. Open .env and paste in your bot token "
            "from https://discord.com/developers/applications (Bot tab)."
        )
        sys.exit(1)

    bot = WarframeBot()

    @bot.event
    async def on_ready() -> None:
        user = bot.user
        log.info("Logged in as %s (ID: %s)", user, getattr(user, "id", "?"))
        if bot.application_id:
            log.info("User-install invite URL: %s", build_invite_url(bot.application_id))

        registered = sorted(bot.get_all_application_commands(), key=lambda c: c.name)
        log.info(
            "Locally defined application commands (%d): %s",
            len(registered),
            ", ".join(c.name for c in registered) or "<none - cog failed to load!>",
        )

        # nextcord already auto-syncs commands with Discord on connect, but we
        # redo it here (harmless/idempotent) purely so any sync failure (e.g.
        # a missing "applications.commands" OAuth scope) shows up loudly in
        # the console instead of failing silently.
        #
        # IMPORTANT: this is always a *global* sync (no guild_id). Registering
        # "/wf" as a guild-specific command would make it show up instantly in
        # one test server, but guild-scoped commands can never appear for
        # user-installs in DMs/other servers - only global commands can. So
        # there's no "fast test guild" shortcut here; global propagation is
        # usually quick in practice (often under a few minutes), even though
        # Discord's documented worst case is up to ~1 hour.
        try:
            await bot.sync_application_commands()
            log.info(
                "Synced application commands globally. If '/wf' doesn't show "
                "up immediately, give it a few minutes (Discord's global "
                "command cache can take a little while to propagate) and "
                "restart your Discord client (Ctrl+R)."
            )
        except nextcord.Forbidden:
            log.exception(
                "Discord rejected the command sync with 403 Forbidden. This "
                "almost always means the app wasn't installed/invited with the "
                "'applications.commands' OAuth scope - recheck the "
                "Installation tab at https://discord.com/developers/applications."
            )
        except Exception:
            log.exception("Failed to sync application commands with Discord.")

    bot.run(TOKEN)


if __name__ == "__main__":
    main()
