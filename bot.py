"""Entry point for WarframeBOT.

A Discord **user-installable app** (works in servers it's added to, in DMs and
in group DMs for anyone who installs it on their account) for WARFRAME:

    /wf   the official WARFRAME Wiki (https://wiki.warframe.com)
    /wm   warframe.market prices, orders, contracts, players and your own listings

Run:  python bot.py     (reads .env; see ENVIRONMENT_VARIABLES.txt)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import aiohttp
import nextcord
from dotenv import load_dotenv
from nextcord.ext import commands

from market.client import WarframeMarketClient
from wiki.client import USER_AGENT, WarframeWikiClient
from wiki.kinds import all_categories

load_dotenv()
log = logging.getLogger("warframebot")

EXTENSIONS = ("cogs.wiki_commands", "cogs.market_commands")


def setup_logging() -> None:
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    path = os.getenv("LOG_FILE", "log.log")
    if path:
        file = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        file.setFormatter(fmt)
        root.addHandler(file)
    logging.getLogger("nextcord").setLevel(max(level, logging.WARNING))


class WarframeBot(commands.Bot):
    def __init__(self) -> None:
        # Slash commands only: no privileged intents (message content, members,
        # presences) are needed or requested.
        intents = nextcord.Intents.default()
        intents.message_content = False
        intents.members = False
        intents.presences = False
        super().__init__(intents=intents)
        self.session: aiohttp.ClientSession | None = None
        self.wiki: WarframeWikiClient | None = None
        self.market: WarframeMarketClient | None = None
        for ext in EXTENSIONS:
            self.load_extension(ext)

    async def start(self, token: str, *, reconnect: bool = True) -> None:
        self.session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT},
                                             timeout=aiohttp.ClientTimeout(total=20))
        self.wiki = WarframeWikiClient(self.session)
        self.market = WarframeMarketClient(self.session, platform=os.getenv("WFM_PLATFORM", "pc"))
        asyncio.create_task(self._warm_caches())
        await super().start(token, reconnect=reconnect)

    async def _warm_caches(self) -> None:
        """Load the big lists autocomplete needs, so the first keystrokes are fast."""
        jobs = {
            "market items": self.market.items(),
            "riven weapons": self.market.riven_weapon_catalog(),
            "riven stats": self.market.riven_attribute_catalog(),
            "lich weapons": self.market.weapon_catalog("lich"),
            "sister weapons": self.market.weapon_catalog("sister"),
        }
        for name, job in jobs.items():
            try:
                log.info("Loaded %d %s", len(await job), name)
            except Exception as exc:
                log.warning("Could not preload %s: %s", name, exc)
        for cat in all_categories():
            try:
                await self.wiki.category_members(cat)
            except Exception as exc:
                log.warning("Could not preload wiki category %s: %s", cat, exc)

    async def close(self) -> None:
        market_cog = self.get_cog("MarketCommands")
        if market_cog is not None:
            try:
                await market_cog.aclose()
            except Exception:
                log.exception("Error while closing the market cog")
        if self.session is not None and not self.session.closed:
            await self.session.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Logged in as %s (ID: %s)", self.user, getattr(self.user, "id", "?"))
        if self.application_id:
            log.info("User-install invite URL: %s", build_invite_url(self.application_id))
        names = sorted(c.name for c in self.get_all_application_commands())
        log.info("Application commands: %s", ", ".join(names) or "<none - a cog failed to load!>")
        # nextcord already syncs on connect; doing it again (idempotent) makes a
        # failure, e.g. a missing applications.commands scope, show up in the log.
        # Always a *global* sync: guild-only commands never appear for user installs.
        try:
            await self.sync_application_commands()
            log.info("Synced commands globally. New commands can take a few minutes to show up "
                     "(restart Discord with Ctrl+R if they don't).")
        except nextcord.Forbidden:
            log.exception("Discord refused the command sync (403). Re-check the Installation tab: the "
                          "app needs the applications.commands scope.")
        except Exception:
            log.exception("Failed to sync application commands with Discord.")


def build_invite_url(application_id: int) -> str:
    """Invite link that adds this app to a user's own account (user install)."""
    return ("https://discord.com/oauth2/authorize"
            f"?client_id={application_id}&integration_type=1&scope=applications.commands")


def main() -> None:
    setup_logging()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        log.error("DISCORD_TOKEN is not set. Put your bot token from "
                  "https://discord.com/developers/applications (Bot tab) in .env.")
        sys.exit(1)
    WarframeBot().run(token)


if __name__ == "__main__":
    main()
