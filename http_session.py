"""Share the bot's aiohttp session, or own one that is closed on shutdown.

"Unclosed client session" warnings happen when a ClientSession is created and
the event loop stops before anyone awaited .close(). Cog.cog_unload is NOT
called on a normal Ctrl+C / bot.close(), so this helper hooks bot.close().

    self.sessions = SessionProvider(bot)
    session = self.sessions.get()          # in any coroutine
"""
from __future__ import annotations

from typing import Optional

# where an existing session might already live on the bot (first open one wins)
DEFAULT_SOURCES = ("session", "wiki.session", "wiki._session", "http_session")


def _dig(obj, dotted: str):
    for part in dotted.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


class SessionProvider:
    def __init__(self, bot, *, sources=DEFAULT_SOURCES, timeout: float = 15):
        self.bot = bot
        self.sources = tuple(sources)
        self.timeout = timeout
        self._own = None
        self._hooked = False

    def get(self):
        """Return an open session. Must be called from inside the running loop."""
        for path in self.sources:
            s = _dig(self.bot, path)
            if s is not None and getattr(s, "closed", True) is False:
                return s                       # the bot's own session: it closes it
        if self._own is None or self._own.closed:
            import aiohttp
            self._own = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout))
            self._hook_bot_close()
        return self._own

    def _hook_bot_close(self) -> None:
        if self._hooked:
            return
        self._hooked = True
        original_close = self.bot.close

        async def close_with_session(*args, **kwargs):
            await self.close()
            return await original_close(*args, **kwargs)

        self.bot.close = close_with_session

    async def close(self) -> None:
        own, self._own = self._own, None
        if own is not None and not own.closed:
            await own.close()
