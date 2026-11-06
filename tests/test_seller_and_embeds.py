"""Seller request building, new embeds (within Discord limits), and the command tree."""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import nextcord  # noqa: E402

from fakes import ITEMS, RouteSession, order, run, user  # noqa: E402
from market import embeds as M  # noqa: E402
from market.client import WarframeMarketClient  # noqa: E402
from market.insights import history_summary, set_report  # noqa: E402
from market.models import (  # noqa: E402
    Item, ItemDetail, StatPoint, parse_item_detail, parse_order, parse_user, parse_weapon_auction,
)
from market.seller import MarketSeller, SellerError  # noqa: E402


def check_limits(embed):
    """Discord's hard limits, which the real nextcord.Embed doesn't enforce locally."""
    d = embed.to_dict()
    assert len(d.get("title", "")) <= 256
    assert len(d.get("description", "")) <= 4096
    assert len(d.get("fields", [])) <= 25
    for f in d.get("fields", []):
        assert 0 < len(f["name"]) <= 256 and 0 < len(f["value"]) <= 1024, f
    total = len(d.get("title", "")) + len(d.get("description", "")) + sum(
        len(f["name"]) + len(f["value"]) for f in d.get("fields", [])) + len(d.get("footer", {}).get("text", ""))
    assert total <= 6000
    return d


# ------------------------------------------------------------------ seller
class FakeSeller(MarketSeller):
    def __init__(self, market, answers):
        super().__init__(market)
        self._jwt = "secret-token-value"
        self.sent = []
        self.answers = answers

    async def _send(self, method, url, *, body=None, platform="pc", signin=False):
        self.sent.append((method, url, body))
        status, data = self.answers.pop(0) if self.answers else (200, {"data": {}})
        return status, {}, data


def market():
    return WarframeMarketClient(RouteSession({"/v2/items": ITEMS}), min_interval=0)


def test_create_order_body_and_rank_rules():
    m = market()
    serration = run(m.resolve_item("serration"))
    s = FakeSeller(m, [(200, {"data": {"id": "o1"}})])
    listed = run(s.create_order(serration, order_type="sell", platinum=20, rank=10, quantity=2))
    method, url, body = s.sent[0]
    assert (method, url.endswith("/v2/order")) == ("POST", True)
    assert body == {"itemId": "m1", "type": "sell", "platinum": 20, "quantity": 2, "visible": True, "rank": 10}
    assert listed.id == "o1" and listed.item_name == "Serration" and listed.rank == 10
    for bad_rank in (None, 11):
        try:
            run(s.create_order(serration, order_type="sell", platinum=20, rank=bad_rank))
        except SellerError:
            pass
        else:
            raise AssertionError("rank should be validated")
    bp = run(m.resolve_item("saryn prime blueprint"))
    run(s.create_order(bp, order_type="buy", platinum=5, rank=3))
    assert "rank" not in s.sent[-1][2]                     # unrankable: rank dropped


def test_update_close_and_my_orders_use_item_index():
    m = market()
    s = FakeSeller(m, [
        (200, {"data": {"id": "o1", "itemId": "m1", "type": "sell", "platinum": 25, "quantity": 1}}),
        (200, {"data": {"id": "o1", "itemId": "m1", "type": "sell", "platinum": 25, "quantity": 0}}),
        (200, {"data": [{"id": "o2", "itemId": "p2", "type": "buy", "platinum": 4},
                        {"id": "o1", "itemId": "m1", "type": "sell", "platinum": 25}]}),
    ])
    up = run(s.update_order("o1", platinum=25, quantity=None))
    assert s.sent[0][0] == "PATCH" and s.sent[0][2] == {"platinum": 25} and up.item_name == "Serration"
    closed = run(s.close_order("o1"))
    assert s.sent[1][1].endswith("/order/o1/close") and closed.quantity == 0
    mine = run(s.get_my_orders())
    assert [o["item"] for o in mine] == ["Serration", "Saryn Prime Systems Blueprint"]   # sells first
    try:
        run(s.update_order("o1"))
    except SellerError as exc:
        assert "Nothing to change" in str(exc)


def test_static_token_401_explains_and_token_never_logged(caplog):
    s = FakeSeller(market(), [(401, {"error": "bad"})])
    s._static_token = True
    with caplog.at_level(logging.DEBUG):
        try:
            run(s.delete_order("o1"))
        except SellerError as exc:
            assert "/wm link" in str(exc)
        else:
            raise AssertionError("expected SellerError")
    assert "secret-token-value" not in caplog.text


def test_headers_never_send_set_cookie():
    s = MarketSeller(market())
    s._jwt = "tok"
    h = s._headers()
    assert h["Authorization"] == "JWT tok" and "Set-Cookie" not in h


# ------------------------------------------------------------------ embeds
def test_item_embed_shows_trading_facts_and_links():
    detail = parse_item_detail({"id": "m1", "slug": "serration", "maxRank": 10, "tradingTax": 2000,
                                "baseEndo": 30, "endoMultiplier": 1, "rarity": "uncommon", "ducats": None,
                                "i18n": {"en": {"name": "Serration", "description": "Damage." * 400,
                                                "wikiLink": "https://wiki.warframe.com/w/Serration"}}})
    top = {"sell": [parse_order(order("1", "sell", 20))], "buy": [parse_order(order("2", "buy", 15))]}
    d = check_limits(M.item_embed(detail, top))
    text = str(d)
    assert "2,000 credits" in text and "330" in text and "WARFRAME Wiki" in text and "20p" in text


def test_top_set_history_user_and_contract_embeds_fit():
    item = Item("serration", "Serration", id="m1", thumb="t.png")
    top = {"sell": [parse_order(order(str(i), "sell", 10 + i, rank=10)) for i in range(5)],
           "buy": [parse_order(order(str(i), "buy", 9 - i)) for i in range(5)]}
    assert "/w T0" in str(check_limits(M.top_embed(item, top, rank=10)))
    assert "Nobody online" in str(check_limits(M.top_embed(item, {"sell": [], "buy": []})))

    root = ItemDetail(item=Item("s_set", "S Set", tags=("set",)), set_root=True)
    part = ItemDetail(item=Item("s_bp", "S Blueprint", ducats=45), quantity_in_set=2)
    rep = set_report([root, part], {"s_set": [parse_order(order("a", "sell", 50))],
                                    "s_bp": [parse_order(order("b", "sell", 10))]})
    assert "+30p" in str(check_limits(M.set_embed(rep)))

    pts = [StatPoint(f"2026-08-{i + 1:02d}", 3, 5, 9, 7, 7) for i in range(30)]
    d = check_limits(M.history_embed(item, history_summary(pts), history_summary(pts[:2], period="48hours")))
    assert "90 days" in d["description"]
    assert "no closed trades" in M.history_embed(item, history_summary([])).description

    u = parse_user({**user("Tenno"), "slug": "tenno", "about": "hi"})
    names = {"m1": item}
    orders = [parse_order({"id": str(i), "type": "sell" if i % 2 else "buy", "platinum": i + 1, "itemId": "m1"},
                          owner=user("Tenno")) for i in range(30)]
    sections = M.user_sections(u, orders, names)
    assert [s.label for s in sections] == ["Profile", "Selling (15)", "Buying (15)"]
    for s in sections:
        for page in s.pages:
            check_limits(page)

    auctions = [parse_weapon_auction({"id": str(i), "buyout_price": 100 + i, "owner": {"ingame_name": "x",
                                      "status": "ingame"}, "item": {"type": "sister", "weapon_url_name": "w",
                                      "element": "heat", "damage": 40, "having_ephemera": i % 2 == 0}})
                for i in range(13)]
    pages = M.weapon_auction_pages("Tenet Arca Plasmor", auctions, kind="sister")
    assert len(pages) == 3 and "Sister of Parvos" in pages[0].title
    for p in pages:
        check_limits(p)


def test_recent_and_ducat_pages():
    item = Item("x", "X Prime Barrel", id="p1", ducats=100)
    recent = [parse_order(order(str(i), "sell" if i % 2 else "buy", i + 1)) for i in range(25)]
    pages = M.recent_pages(recent, {"p1": item}, side="sell")
    assert len(pages) == 2 and all("WTS" in line for line in pages[0].description.splitlines())
    assert "No recent orders" in M.recent_pages([], {})[0].description
    from market.insights import DucatDeal
    deal_pages = M.ducat_deal_pages([DucatDeal(item, 100, i + 1, "s") for i in range(12)])
    assert len(deal_pages) == 2 and "100.0" in deal_pages[0].description
    for p in pages + deal_pages:
        check_limits(p)


# ------------------------------------------------------------ command tree
def test_command_tree_is_valid_for_discord():
    from nextcord.ext import commands

    async def build():
        bot = commands.Bot(intents=nextcord.Intents.default())
        bot.load_extension("cogs.wiki_commands")
        bot.load_extension("cogs.market_commands")
        return {c.name: c for c in bot.get_all_application_commands()}

    cmds = asyncio.new_event_loop().run_until_complete(build())
    assert set(cmds) == {"wf", "wm"}
    for cmd in cmds.values():
        assert len(cmd.children) <= 25
        for sub in cmd.children.values():
            sub.from_autocomplete()
        payload = cmd.get_payload(None)
        for sub in payload["options"]:
            assert 0 < len(sub["description"]) <= 100, sub["name"]
            seen_optional = False
            for opt in sub.get("options", []):
                assert len(opt["description"]) <= 100, (sub["name"], opt["name"])
                assert not (opt.get("required") and seen_optional), (sub["name"], opt["name"])
                seen_optional = seen_optional or not opt.get("required")
    assert {"item", "set", "history", "top", "user", "lich", "sister", "ducat_deals", "recent", "sold",
            "status"} <= set(cmds["wm"].children)
    assert {"mod", "relic", "compare", "drops", "random", "arcane", "enemy"} <= set(cmds["wf"].children)
