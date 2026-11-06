"""Run each /wm and /wf command body end-to-end against fake upstreams and a
fake Discord interaction, so a typo in a cog shows up offline."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import nextcord  # noqa: E402
from nextcord.ext import commands  # noqa: E402

from fakes import ITEMS, RouteSession, order, user, v2  # noqa: E402
from market.client import WarframeMarketClient  # noqa: E402
from wiki.client import WarframeWikiClient  # noqa: E402

SET = v2({"items": [
    {"id": "set1", "slug": "saryn_prime_set", "setRoot": True, "i18n": {"en": {"name": "Saryn Prime Set"}}},
    {"id": "p1", "slug": "saryn_prime_blueprint", "ducats": 45, "i18n": {"en": {"name": "Saryn Prime Blueprint"}}}]})
TOP = v2({"sell": [order("1", "sell", 30, "m1")], "buy": [order("2", "buy", 20, "m1")]})
STATS = {"payload": {"statistics_closed": {"48hours": [], "90days": [
    {"datetime": f"2026-09-{d:02d}", "volume": 3, "avg_price": 10 + d, "median": 10 + d} for d in range(1, 29)]}}}
AUCTION = {"payload": {"auctions": [
    {"id": "r1", "buyout_price": 300, "owner": {"ingame_name": "R", "status": "ingame"},
     "item": {"type": "riven", "weapon_url_name": "acceltra", "name": "crita-satitis", "mod_rank": 8,
              "polarity": "madurai", "attributes": [{"url_name": "critical_damage", "value": 120, "positive": True}]}},
    {"id": "l1", "buyout_price": 500, "owner": {"ingame_name": "L", "status": "online"},
     "item": {"type": "lich", "weapon_url_name": "kuva_bramma", "element": "heat", "damage": 60}}]}}
MARKET = {
    "/v2/items": ITEMS, "/set": SET, "/top": TOP, "/statistics": STATS, "/v1/auctions/search": AUCTION,
    "/v2/orders/item/serration": v2([order("1", "sell", 30, "m1", rank=10), order("2", "buy", 25, "m1", rank=10)]),
    "/v2/item/serration": v2({"id": "m1", "slug": "serration", "maxRank": 10, "tradingTax": 2000,
                              "i18n": {"en": {"name": "Serration"}}}),
    "/v2/item/saryn_prime_blueprint": v2({"id": "p1", "slug": "saryn_prime_blueprint", "ducats": 45,
                                          "i18n": {"en": {"name": "Saryn Prime Blueprint"}}}),
    "/v2/item/serration/set": v2({"items": [{"id": "m1", "slug": "serration", "i18n": {"en": {"name": "Serration"}}}]}),
    "/v2/orders/recent": v2([order("3", "sell", 5, "p1"), order("4", "sell", 9, "p1"), order("5", "buy", 3, "p1")]),
    "/v2/user/tenno": v2({**user("Tenno"), "slug": "tenno"}),
    "/v2/orders/user/tenno": v2([{"id": "9", "type": "sell", "platinum": 7, "itemId": "m1"}]),
    "/v2/riven/weapons": v2([{"slug": "acceltra", "disposition": 0.75, "i18n": {"en": {"name": "Acceltra"}}}]),
    "/v2/riven/attributes": v2([{"slug": "critical_damage"}]),
    "/v2/lich/weapons": v2([{"slug": "kuva_bramma", "i18n": {"en": {"name": "Kuva Bramma"}}}]),
}
PAGE_HTML = ('<div class="infobox"><img src="/images/x.png"></div>'
             '<div class="group mw-collapsible"><div class="header">General Information</div>'
             '<div class="row"><div class="label left">Polarity</div><div class="value right">Madurai</div></div></div>'
             '<p>Serration is a mod that increases damage on rifles.</p>'
             '<h2>Acquisition</h2><table><tr><td>Market</td><td>2,500 credits</td></tr></table>')
class WikiSession(RouteSession):
    """Answers like MediaWiki's api.php, by action."""
    closed = False

    def __init__(self):
        super().__init__({})

    def get(self, url, params=None, headers=None, **kwargs):
        from fakes import FakeResp
        self.calls.append((url, params, headers))
        action = (params or {}).get("action")
        if action == "opensearch":
            return FakeResp(200, ["q", ["Serration"], [""], ["https://wiki.warframe.com/w/Serration"]])
        if action == "parse":
            return FakeResp(200, {"parse": {"title": "Serration", "text": PAGE_HTML, "categories": []}})
        return FakeResp(200, {"query": {"pages": [{"title": "Serration"}], "random": [{"title": "Serration"}],
                                        "categorymembers": [{"title": "Serration"}]}})


class Followup:
    def __init__(self, sink):
        self.sink = sink

    async def send(self, content=None, **kw):
        self.sink.append({"content": content, **kw})
        return SimpleNamespace(edit=None)


class Response:
    def __init__(self, sink):
        self.sink, self.deferred = sink, False

    async def defer(self, **kw):
        self.deferred = True

    async def send_message(self, content=None, **kw):
        self.sink.append({"content": content, **kw})

    def is_done(self):
        return self.deferred


class FakeInteraction:
    def __init__(self, user_id=1):
        self.sent = []
        self.user = SimpleNamespace(id=user_id)
        self.response = Response(self.sent)
        self.followup = Followup(self.sent)

    async def send(self, content=None, **kw):
        self.sent.append({"content": content, **kw})
        return SimpleNamespace()


LOOP = asyncio.new_event_loop()


def setup_bot():
    async def make():
        bot = commands.Bot(intents=nextcord.Intents.default())
        bot.market = WarframeMarketClient(RouteSession(dict(MARKET)), min_interval=0, retry_delay=0)
        bot.wiki = WarframeWikiClient(WikiSession())
        bot.session = WikiSession()
        bot.load_extension("cogs.market_commands")
        bot.load_extension("cogs.wiki_commands")
        return bot
    return LOOP.run_until_complete(make())


BOT = setup_bot()


def call(group, sub, **kwargs):
    cmd = next(c for c in BOT.get_all_application_commands() if c.name == group).children[sub]
    cog = BOT.get_cog("MarketCommands" if group == "wm" else "WikiCommands")
    ix = FakeInteraction()
    defaults = {o.functional_name: o.default for o in cmd.options.values()}
    LOOP.run_until_complete(cmd.callback(cog, ix, **{**defaults, **kwargs}))
    assert ix.sent, f"/{group} {sub} sent nothing"
    return ix.sent[-1]


def text(msg):
    parts = [msg.get("content") or ""]
    e = msg.get("embed")
    if e is not None:
        parts += [e.title or "", e.description or ""] + [f"{f.name} {f.value}" for f in e.fields]
    return "\n".join(parts)


def test_market_commands_end_to_end():
    assert "seller view" in text(call("wm", "price", item="serration", rank=10, my_price=29))
    assert "Buyers to whisper" in text(call("wm", "buyers", item="serration"))
    assert "Cheapest sellers" in text(call("wm", "sellers", item="serration"))
    assert "best orders" in text(call("wm", "top", item="serration"))
    assert "2,000 credits" in text(call("wm", "item", item="serration"))
    assert "set vs parts" in text(call("wm", "set", item="saryn prime set"))
    assert "price history" in text(call("wm", "history", item="serration"))
    assert "ducats vs platinum" in text(call("wm", "ducats", item="saryn prime blueprint"))
    assert "Best ducats per platinum" in text(call("wm", "ducat_deals", min_ducats=1))
    assert "Recent market trends" in text(call("wm", "trends"))
    assert "Newest orders" in text(call("wm", "recent", side="sell"))
    assert "Reputation" in text(call("wm", "user", name="Tenno"))
    riven = text(call("wm", "riven", weapon="acceltra", stat1="crit damage"))
    assert "crita-satitis" in riven and "disposition" in riven
    assert "60% Heat" in text(call("wm", "lich", weapon="bramma", element="heat"))


def test_market_errors_are_friendly():
    assert "No tradable item" in text(call("wm", "price", item="zzzzqqqq"))
    assert "isn't part of a set" in text(call("wm", "set", item="serration"))
    assert "No warframe.market user" in text(call("wm", "user", name="nobody"))


def test_owner_commands_refuse_strangers(monkeypatch):
    monkeypatch.setenv("OWNER_ID", "999")
    for name, kw in (("list", {"item": "serration", "price": 5}), ("my_orders", {}),
                     ("unlist", {"order_id": "x"}), ("status", {"status": "ingame"})):
        assert "Only the bot owner" in text(call("wm", name, **kw))


def test_wiki_commands_end_to_end():
    assert "Serration" in text(call("wf", "mod", name="Serration"))
    drops = call("wf", "drops", name="Serration")
    assert "2,500 credits" in text(drops)
    assert "vs" in text(call("wf", "compare", first="Serration", second="Serration"))
    assert "Serration" in text(call("wf", "random"))
