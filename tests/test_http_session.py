import asyncio
import sys
import types


class FakeSession:
    def __init__(self, **kw):
        self.closed = False
        self.close_calls = 0

    async def close(self):
        self.close_calls += 1
        self.closed = True


def _install_fake_aiohttp():
    m = types.ModuleType("aiohttp")
    m.ClientSession = FakeSession
    m.ClientTimeout = lambda **kw: None
    sys.modules["aiohttp"] = m


class Bot:
    def __init__(self):
        self.closed_bot = False

    async def close(self):
        self.closed_bot = True


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


def test_reuses_open_bot_session_and_never_creates_own():
    _install_fake_aiohttp()
    from http_session import SessionProvider
    bot = Bot()
    bot.session = FakeSession()
    p = SessionProvider(bot)
    assert p.get() is bot.session and p._own is None and not p._hooked


def test_finds_session_on_wiki_client():
    _install_fake_aiohttp()
    from http_session import SessionProvider
    bot = Bot()
    bot.wiki = types.SimpleNamespace(_session=FakeSession())
    assert SessionProvider(bot).get() is bot.wiki._session


def test_skips_closed_bot_session_and_owns_one_that_closes_with_bot():
    _install_fake_aiohttp()
    from http_session import SessionProvider
    bot = Bot()
    bot.session = FakeSession()
    bot.session.closed = True
    p = SessionProvider(bot)
    mine = p.get()
    assert mine is not bot.session and p.get() is mine        # created once, reused
    run(bot.close())                                          # simulates Ctrl+C shutdown
    assert mine.closed and mine.close_calls == 1 and bot.closed_bot


def test_close_is_idempotent_and_recreates_after_close():
    _install_fake_aiohttp()
    from http_session import SessionProvider
    bot = Bot()
    p = SessionProvider(bot)
    first = p.get()
    run(p.close())
    run(p.close())
    assert first.close_calls == 1
    assert p.get() is not first
