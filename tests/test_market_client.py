import asyncio
import time

from market.client import ItemNotFound, WarframeMarketClient, MarketError

ITEMS = {"data": [
    {"slug": "mirage_prime_set", "i18n": {"en": {"name": "Mirage Prime Set", "icon": "a.png"}}},
    {"slug": "mirage_prime_systems", "i18n": {"en": {"name": "Mirage Prime Systems"}}},
    {"slug": "serration", "i18n": {"en": {"name": "Serration"}}},
    {"slug": "primed_continuity", "i18n": {"en": {"name": "Primed Continuity"}}},
]}
ORDERS = {"data": [
    {"id": "1", "type": "sell", "platinum": 30, "quantity": 1, "visible": True,
     "user": {"ingameName": "A", "status": "ingame"}},
    {"id": "2", "type": "buy", "platinum": 20, "quantity": 1, "visible": True,
     "user": {"ingameName": "B", "status": "online"}},
]}
STATS = {"payload": {"statistics_closed": {"48hours": [
    {"volume": 10, "avg_price": 30, "median": 29}, {"volume": 30, "avg_price": 40, "median": 38}]}}}


class FakeResp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, headers))
        if url.endswith("/items"):
            return FakeResp(200, ITEMS)
        if "/orders/item/" in url:
            return FakeResp(404, {}) if "nope" in url else FakeResp(200, ORDERS)
        if url.endswith("/statistics"):
            return FakeResp(200, STATS)
        return FakeResp(500, {})


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_search_ranking_substring_and_typo():
    c = WarframeMarketClient(FakeSession())
    assert run(c.search_items("mirage prime set", 3))[0].slug == "mirage_prime_set"
    assert {i.slug for i in run(c.search_items("mirage", 5))} == {"mirage_prime_set", "mirage_prime_systems"}
    assert run(c.search_items("serrration", 3))[0].slug == "serration"        # typo -> fuzzy
    assert run(c.search_items("zzzzzz", 3)) == []


def test_item_list_loaded_once_and_cached():
    s = FakeSession()
    c = WarframeMarketClient(s)
    run(c.search_items("serration"))
    run(c.search_items("mirage"))
    assert sum(1 for u, _ in s.calls if u.endswith("/items")) == 1


def test_orders_headers_cache_and_parse():
    s = FakeSession()
    c = WarframeMarketClient(s, platform="ps4", crossplay=False)
    orders = run(c.get_orders("mirage_prime_set"))
    run(c.get_orders("mirage_prime_set"))                                     # cached
    assert len(orders) == 2 and len([u for u, _ in s.calls if "/orders/item/" in u]) == 1
    h = s.calls[0][1]
    assert h["Platform"] == "ps4" and h["Crossplay"] == "false" and "WarframeBOT" in h["User-Agent"]
    run(c.get_orders("mirage_prime_set", platform="pc"))                      # different key
    assert len([u for u, _ in s.calls if "/orders/item/" in u]) == 2


def test_not_found_and_resolve():
    c = WarframeMarketClient(FakeSession())
    try:
        run(c.get_orders("nope"))
        assert False, "should raise"
    except ItemNotFound:
        pass
    try:
        run(c.resolve_item("zzzzzz"))
        assert False, "should raise"
    except MarketError:
        pass


def test_history_weighted_average():
    h = run(WarframeMarketClient(FakeSession()).get_history("serration"))
    assert h["volume_48h"] == 40 and h["avg_price_48h"] == 37.5               # (10*30+30*40)/40


def test_history_failure_returns_none():
    class Boom(FakeSession):
        def get(self, url, params=None, headers=None):
            return FakeResp(500, {})
    assert run(WarframeMarketClient(Boom()).get_history("serration")) is None


def test_rate_limiter_spaces_calls():
    c = WarframeMarketClient(FakeSession(), min_interval=0.1, cache_ttl=0)
    async def burst():
        await asyncio.gather(*[c.get_orders("serration") for _ in range(4)])

    t = time.monotonic()
    run(burst())
    assert time.monotonic() - t >= 0.27                                       # 3 gaps of ~0.1s
