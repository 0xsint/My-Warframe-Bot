"""Offline tests. Run from the project root:  python -m unittest discover -s tests -v

aiohttp and nextcord are stubbed when not installed, so these exercise *our*
code (parsing, rate limiting, retries, caching, analysis, embed building) but
not the real libraries or the live API.
"""

import asyncio
import sys
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---- stubs (only if the real packages are missing) ------------------------- #
try:
    import aiohttp  # noqa: F401
except ImportError:
    aio = types.ModuleType("aiohttp")

    class ClientError(Exception): ...

    class ClientTimeout:
        def __init__(self, total=None): self.total = total

    aio.ClientError, aio.ClientTimeout, aio.ClientSession = ClientError, ClientTimeout, object
    sys.modules["aiohttp"] = aio

try:
    import nextcord  # noqa: F401
except ImportError:
    nc = types.ModuleType("nextcord")

    class Embed:
        def __init__(self, title=None, url=None, color=None, description=None):
            self.title, self.url, self.color, self.description = title, url, color, description
            self.fields, self.footer = [], None

        def add_field(self, name, value, inline=True):
            assert len(name) <= 256, "field name too long"
            assert len(value) <= 1024, f"field value too long ({len(value)})"
            self.fields.append((name, value, inline))

        def set_footer(self, text): self.footer = text

    nc.Embed = Embed
    utils = types.ModuleType("nextcord.utils")
    utils.escape_markdown = lambda s: s.replace("_", "\\_").replace("*", "\\*")
    nc.utils = utils
    sys.modules["nextcord"], sys.modules["nextcord.utils"] = nc, utils

import aiohttp  # noqa: E402

from market import analysis as A  # noqa: E402
from market import embeds  # noqa: E402
from market.client import (  # noqa: E402
    AmbiguousItem, Item, ItemIndex, ItemNotFound, MarketError, MarketUnavailable,
    Order, WarframeMarketClient,
)


# ---- fake HTTP -------------------------------------------------------------- #
class FakeResp:
    def __init__(self, status, body, headers=None):
        self.status, self._body, self.headers = status, body, headers or {}

    async def json(self, content_type=None): return self._body
    async def text(self): return "Just a moment... cf challenge" if self.status == 403 else ""
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class FakeSession:
    """Routes by URL suffix; each route is a list of responses consumed in order."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, dict(headers or {})))
        for suffix, queue in self.routes.items():
            if url.endswith(suffix):
                item = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(item, Exception):
                    raise item
                return item
        return FakeResp(404, {})


def run(coro): return asyncio.run(coro)


def mk_item(slug, name, **kw):
    return Item(id=slug, slug=slug, name=name, **kw)


def mk_order(type_, plat, status="ingame", qty=1, rank=None, user="Tenno", rep=5):
    return Order(id=f"{type_}{plat}{user}", type=type_, platinum=plat, quantity=qty, rank=rank,
                 status=status, user=user, reputation=rep, platform="pc")


def api_order(type_, plat, status="ingame", user="Tenno", qty=1, **extra):
    d = {"id": f"{user}{plat}", "type": type_, "platinum": plat, "quantity": qty, "visible": True,
         "user": {"ingameName": user, "status": status, "reputation": 3, "platform": "pc"}}
    d.update(extra)
    return d


ITEMS_PAYLOAD = {"data": [
    {"id": "1", "slug": "ash_prime_set", "tags": ["set"], "setRoot": True,
     "i18n": {"en": {"name": "Ash Prime Set"}}},
    {"id": "2", "slug": "ash_prime_systems", "tags": ["component"], "quantityInSet": 1,
     "i18n": {"en": {"name": "Ash Prime Systems"}}},
    {"id": "3", "slug": "ash_prime_chassis", "i18n": {"en": {"name": "Ash Prime Chassis"}}},
    {"id": "4", "slug": "serration", "maxRank": 10, "i18n": {"en": {"name": "Serration"}}},
    {"id": "5", "slug": "mirage_prime_set", "i18n": {"en": {"name": "Mirage Prime Set"}}},
    {"id": "6", "slug": "mirage_prime_systems", "i18n": {"en": {"name": "Mirage Prime Systems"}}},
]}


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.idx = ItemIndex([Item.from_api(d) for d in ITEMS_PAYLOAD["data"]])

    def test_resolve_exact_name_slug_and_loose(self):
        self.assertEqual(self.idx.resolve("Ash Prime Set").slug, "ash_prime_set")
        self.assertEqual(self.idx.resolve("ash_prime_set").slug, "ash_prime_set")
        self.assertEqual(self.idx.resolve("  ash  PRIME   set ").slug, "ash_prime_set")

    def test_ambiguous_raises_with_candidates(self):
        with self.assertRaises(AmbiguousItem) as cm:
            self.idx.resolve("ash prime")
        self.assertGreaterEqual(len(cm.exception.candidates), 2)

    def test_unique_prefix_resolves(self):
        self.assertEqual(self.idx.resolve("serr").slug, "serration")

    def test_not_found(self):
        with self.assertRaises(ItemNotFound):
            self.idx.resolve("zzzzqqqq")

    def test_typo_tolerance(self):
        self.assertEqual(self.idx.resolve("seration").slug, "serration")

    def test_search_prefix_first(self):
        names = [i.name for i in self.idx.search("mirage")]
        self.assertTrue(all("Mirage" in n for n in names[:2]))


class ClientTests(unittest.TestCase):
    def client(self, routes, **kw):
        s = FakeSession(routes)
        kw.setdefault("min_interval", 0.0)
        return WarframeMarketClient(s, **kw), s

    def test_orders_parse_filters_hidden_and_sends_headers(self):
        body = {"data": [api_order("sell", 40), api_order("buy", 30, visible=False),
                         {"garbage": True}, api_order("buy", 25, status="offline")]}
        c, s = self.client({"/orders/item/ash_prime_set": [FakeResp(200, body)]})
        orders = run(c.get_orders("ash_prime_set", "xbox"))
        self.assertEqual([(o.type, o.platinum) for o in orders], [("sell", 40), ("buy", 25)])
        url, headers = s.calls[0]
        self.assertTrue(url.startswith("https://api.warframe.market/v2/orders/item/"))
        self.assertEqual(headers["Platform"], "xbox")
        self.assertEqual(headers["Crossplay"], "true")

    def test_cache_and_inflight_dedupe(self):
        c, s = self.client({"/orders/item/x": [FakeResp(200, {"data": [api_order("sell", 5)]})]})

        async def go():
            await asyncio.gather(*(c.get_orders("x") for _ in range(5)))
            await c.get_orders("x")
        run(go())
        self.assertEqual(len(s.calls), 1)

    def test_cache_is_per_platform(self):
        c, s = self.client({"/orders/item/x": [FakeResp(200, {"data": []})]})
        run(c.get_orders("x", "pc")); run(c.get_orders("x", "ps4"))
        self.assertEqual(len(s.calls), 2)

    def test_retry_on_429_then_success(self):
        route = [FakeResp(429, {}, {"Retry-After": "0"}), FakeResp(200, {"data": []})]
        c, s = self.client({"/orders/item/x": route})
        self.assertEqual(run(c.get_orders("x")), [])
        self.assertEqual(len(s.calls), 2)

    def test_gives_up_after_retries(self):
        c, s = self.client({"/orders/item/x": [FakeResp(500, {}, {"Retry-After": "0"})]}, max_retries=2)
        with self.assertRaises(MarketUnavailable):
            run(c.get_orders("x"))
        self.assertEqual(len(s.calls), 2)

    def test_network_error_retried(self):
        route = [aiohttp.ClientError("boom"), FakeResp(200, {"data": []})]
        c, s = self.client({"/orders/item/x": route})
        self.assertEqual(run(c.get_orders("x")), [])

    def test_403_is_unavailable_not_retried(self):
        c, s = self.client({"/orders/item/x": [FakeResp(403, {})]})
        with self.assertRaises(MarketUnavailable):
            run(c.get_orders("x"))
        self.assertEqual(len(s.calls), 1)

    def test_403_logs_diagnostics(self):
        c, _ = self.client({"/orders/item/x": [FakeResp(403, {}, {"Server": "cloudflare", "CF-RAY": "abc"})]})
        with self.assertLogs("warframebot.market", level="ERROR") as cm:
            with self.assertRaises(MarketUnavailable):
                run(c.get_orders("x"))
        out = "\n".join(cm.output)
        self.assertIn("cloudflare", out)
        self.assertIn("Just a moment", out)

    def test_user_agent_override(self):
        c, s = self.client({"/orders/item/x": [FakeResp(200, {"data": []})]}, user_agent="MyUA/1")
        run(c.get_orders("x"))
        self.assertEqual(s.calls[0][1]["User-Agent"], "MyUA/1")
        c2, s2 = self.client({"/orders/item/x": [FakeResp(200, {"data": []})]}, user_agent=None)
        run(c2.get_orders("x"))
        self.assertNotIn("User-Agent", s2.calls[0][1])

    def test_404_is_market_error(self):
        c, _ = self.client({})
        with self.assertRaises(MarketError):
            run(c.get_orders("nope"))

    def test_rate_limit_spacing(self):
        c, s = self.client({"/orders/item/a": [FakeResp(200, {"data": []})],
                            "/orders/item/b": [FakeResp(200, {"data": []})],
                            "/orders/item/c": [FakeResp(200, {"data": []})]}, min_interval=0.15)
        t0 = time.monotonic()

        async def go():
            await asyncio.gather(c.get_orders("a"), c.get_orders("b"), c.get_orders("c"))
        run(go())
        self.assertGreaterEqual(time.monotonic() - t0, 0.28)  # 3 requests => >= 2 gaps

    def test_items_and_stale_fallback(self):
        c, s = self.client({"/items": [FakeResp(200, ITEMS_PAYLOAD), FakeResp(500, {}, {"Retry-After": "0"})]},
                           max_retries=1)
        idx = run(c.load_items())
        self.assertEqual(len(idx), 6)
        c._cache.clear()  # force refresh -> fails -> stale index served
        self.assertIs(run(c.load_items()), idx)

    def test_stats_use_v1_and_unwrap(self):
        body = {"payload": {"statistics_closed": {"48hours": [{"volume": 1}], "90days": [{"volume": 2}]}}}
        c, s = self.client({"/items/x/statistics": [FakeResp(200, body)]})
        st = run(c.get_stats("x"))
        self.assertEqual((st["48hours"][0]["volume"], st["90days"][0]["volume"]), (1, 2))
        self.assertIn("/v1/items/x/statistics", s.calls[0][0])

    def test_get_set(self):
        body = {"data": {"id": "1", "items": ITEMS_PAYLOAD["data"][:3]}}
        c, _ = self.client({"/item/ash_prime_set/set": [FakeResp(200, body)]})
        members = run(c.get_set("ash_prime_set"))
        self.assertEqual([m.set_root for m in members], [True, False, False])


class AnalysisTests(unittest.TestCase):
    def test_summary_sorting_online_filter_and_ties(self):
        item = mk_item("x", "X")
        orders = [mk_order("sell", 50, "online", user="a"), mk_order("sell", 50, "ingame", user="b"),
                  mk_order("sell", 10, "offline", user="c"), mk_order("sell", 70, user="d"),
                  mk_order("buy", 30, user="e"), mk_order("buy", 45, user="f"), mk_order("buy", 99, "offline")]
        s = A.build_summary(item, orders)
        self.assertEqual([o.user for o in s.sells], ["b", "a", "d"])  # ingame beats online on tie
        self.assertEqual(s.lowest_sell.platinum, 50)
        self.assertEqual(s.highest_buy.platinum, 45)
        self.assertEqual(s.spread, 5)
        self.assertFalse(s.crossed)
        self.assertEqual(len(A.build_summary(item, orders, online_only=False).sells), 4)

    def test_crossed_market_detected(self):
        s = A.build_summary(mk_item("x", "X"), [mk_order("sell", 20), mk_order("buy", 25)])
        self.assertTrue(s.crossed)

    def test_rank_filter(self):
        mod = mk_item("serration", "Serration", max_rank=10)
        orders = [mk_order("sell", 5, rank=0), mk_order("sell", 40, rank=10), mk_order("sell", 20, rank=5)]
        self.assertEqual(A.build_summary(mod, orders, rank_mode="max").lowest_sell.platinum, 40)
        self.assertEqual(A.build_summary(mod, orders, rank_mode="unranked").lowest_sell.platinum, 5)
        self.assertEqual(len(A.build_summary(mod, orders, rank_mode="any").sells), 3)
        # non-rankable items ignore the filter entirely
        plain = mk_item("p", "P")
        self.assertEqual(len(A.build_summary(plain, [mk_order("sell", 5)], rank_mode="max").sells), 1)

    def test_purchase_plan_multi_seller_and_budget(self):
        sells = [mk_order("sell", 10, qty=2, user="a"), mk_order("sell", 12, qty=5, user="b"),
                 mk_order("sell", 30, qty=9, user="c")]
        p = A.plan_purchase(sells, 4, max_price=15)
        self.assertEqual([(o.user, n) for o, n in p.legs], [("a", 2), ("b", 2)])
        self.assertEqual((p.total, p.units, p.complete), (44, 4, True))
        short = A.plan_purchase(sells, 10, max_price=15)
        self.assertEqual((short.units, short.shortfall, short.complete), (7, 3, False))
        self.assertEqual(A.plan_purchase(sells, 1, max_price=5).units, 0)

    def test_whisper_format(self):
        w = A.whisper(mk_order("sell", 42, user="Bob"), mk_item("x", "Ash Prime Set"))
        self.assertEqual(w, '/w Bob Hi! I want to buy: "Ash Prime Set" for 42 platinum. (warframe.market)')
        mod = mk_item("s", "Serration", max_rank=10)
        self.assertIn("(rank 10)", A.whisper(mk_order("sell", 9, rank=10), mod))

    def test_set_breakdown(self):
        root = mk_item("set", "Set", set_root=True)
        a, b = mk_item("a", "A"), mk_item("b", "B", quantity_in_set=2)
        rs = A.build_summary(root, [mk_order("sell", 100), mk_order("buy", 90)])
        sa = A.build_summary(a, [mk_order("sell", 20), mk_order("buy", 15)])
        sb = A.build_summary(b, [mk_order("sell", 30), mk_order("buy", 25)])
        bd = A.SetBreakdown(A.SetPart(root, 1, rs), [A.SetPart(a, 1, sa), A.SetPart(b, 2, sb)])
        self.assertEqual((bd.parts_ask, bd.parts_bid), (80, 65))
        self.assertEqual(bd.assemble_margin, 10)   # 90 - 80
        self.assertEqual(bd.break_margin, -35)     # 65 - 100
        self.assertEqual(bd.cheapest_way, ("Buy the parts separately", 80))
        # a part nobody sells -> parts_ask unknown, no bogus numbers
        sa2 = A.build_summary(a, [mk_order("buy", 15)])
        bd2 = A.SetBreakdown(A.SetPart(root, 1, rs), [A.SetPart(a, 1, sa2)])
        self.assertIsNone(bd2.parts_ask)
        self.assertIsNone(bd2.assemble_margin)

    def test_trend(self):
        item = mk_item("x", "X")
        rows = [{"datetime": f"2026-10-{d:02d}T00:00:00.000+00:00", "avg_price": 100 + d,
                 "volume": 10, "min_price": 90, "max_price": 130} for d in range(1, 11)]
        t = A.build_trend(item, {"90days": rows, "48hours": [{"volume": 4}, {"volume": 6}]})
        self.assertEqual(t.volume_48h, 10)
        self.assertEqual(t.volume_7d, 70)
        self.assertAlmostEqual(t.avg_7d, sum(100 + d for d in range(4, 11)) / 7)
        self.assertGreater(t.change_7d_pct, 0)
        self.assertEqual((t.low_90d, t.high_90d), (90, 130))
        self.assertEqual(len(t.daily_prices), 10)
        self.assertIsNone(A.build_trend(item, {"90days": [], "48hours": []}))

    def test_trend_rank_filter(self):
        mod = mk_item("m", "M", max_rank=10)
        rows = [{"datetime": "2026-10-10T00:00:00Z", "avg_price": 5, "volume": 1, "mod_rank": 0},
                {"datetime": "2026-10-10T00:00:00Z", "avg_price": 50, "volume": 1, "mod_rank": 10}]
        self.assertEqual(A.build_trend(mod, {"90days": rows}, "max").avg_7d, 50)
        self.assertEqual(A.build_trend(mod, {"90days": rows}, "unranked").avg_7d, 5)

    def test_sparkline(self):
        self.assertEqual(A.sparkline([1, 2, 3, 4, 5, 6, 7, 8]), "▁▂▃▄▅▆▇█")
        self.assertEqual(A.sparkline([5, 5, 5]), "▄▄▄")
        self.assertEqual(A.sparkline([]), "")


class EmbedTests(unittest.TestCase):
    """Smoke tests: every embed builds, respects field limits, and has sane content."""

    def setUp(self):
        self.item = mk_item("ash_prime_set", "Ash Prime Set", ducats=100)
        many = [mk_order("sell", 40 + i, user=f"Some_Very_Long_User_Name_{i}", qty=3) for i in range(30)]
        buys = [mk_order("buy", 20 + i, user=f"buyer_{i}") for i in range(30)]
        self.summary = A.build_summary(self.item, many + buys)

    def test_price_embed(self):
        e = embeds.price_embed(self.summary, "pc")
        text = e.description + "".join(v for _, v, _ in e.fields)
        self.assertIn("Lowest sell:** 40p", text)
        self.assertIn("Ducats", text)

    def test_price_embed_empty_market(self):
        e = embeds.price_embed(A.build_summary(self.item, []), "pc")
        self.assertIn("No sellers online.", [v for _, v, _ in e.fields])

    def test_compare_embed(self):
        other = A.build_summary(mk_item("m", "Mirage Prime Set"), [mk_order("sell", 25)])
        none = A.build_summary(mk_item("z", "Nobody Sells"), [])
        e = embeds.compare_embed([self.summary, other, none], "pc")
        self.assertLess(e.description.index("Mirage"), e.description.index("Ash"))
        self.assertLess(e.description.index("Ash"), e.description.index("Nobody"))
        self.assertIn("Mirage Prime Set", e.fields[0][1])

    def test_availability_variants(self):
        ok = embeds.availability_embed(self.summary, A.plan_purchase(self.summary.sells, 2, 50), 50, "pc")
        self.assertTrue(ok.title.startswith("✅"))
        self.assertTrue(any("whispers" in n for n, _, _ in ok.fields))
        partial = embeds.availability_embed(self.summary, A.plan_purchase(self.summary.sells, 99, 41), 41, "pc")
        self.assertTrue(partial.title.startswith("⚠️"))
        none = embeds.availability_embed(self.summary, A.plan_purchase(self.summary.sells, 1, 5), 5, "pc")
        self.assertTrue(none.title.startswith("❌"))
        self.assertIn("over your budget", none.description)
        empty = A.build_summary(self.item, [])
        e = embeds.availability_embed(empty, A.plan_purchase(empty.sells, 1), None, "pc")
        self.assertIn("Nobody is selling", e.description)

    def test_set_and_trend_embeds(self):
        root = mk_item("set", "Set", set_root=True)
        part = mk_item("a", "A")
        bd = A.SetBreakdown(A.SetPart(root, 1, A.build_summary(root, [mk_order("sell", 100)])),
                            [A.SetPart(part, 1, A.build_summary(part, [mk_order("sell", 20)]))])
        e = embeds.set_embed(bd, "pc")
        self.assertTrue(any(n == "Verdict" for n, _, _ in e.fields))
        rows = [{"datetime": f"2026-10-{d:02d}T00:00:00Z", "avg_price": 50 + d, "volume": 3,
                 "min_price": 40, "max_price": 70} for d in range(1, 10)]
        t = A.build_trend(self.item, {"90days": rows, "48hours": []})
        self.assertIn("7-day avg", [n for n, _, _ in embeds.trend_embed(t, "pc").fields])


if __name__ == "__main__":
    unittest.main()
