"""Offline tests for the expanded warframe.market client, models and insights."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import ITEMS, RouteSession, order, run, user, v2  # noqa: E402
from market.client import ItemNotFound, MarketError, WarframeMarketClient  # noqa: E402
from market.insights import (  # noqa: E402
    WeaponFilter, ducat_deals, filter_weapon_auctions, history_summary, recent_trends, set_report, sparkline,
)
from market.models import (  # noqa: E402
    Item, RivenWeapon, StatPoint, asking_price, parse_item_detail, parse_order, parse_user,
    parse_weapon_auction,
)

DETAIL = v2({"id": "p2", "slug": "saryn_prime_systems_blueprint", "tags": ["prime"], "ducats": 100,
             "rarity": "rare", "tradingTax": 6000, "reqMasteryRank": 0, "quantityInSet": 1,
             "i18n": {"en": {"name": "Saryn Prime Systems Blueprint", "description": "Saryn systems.",
                             "wikiLink": "https://wiki.warframe.com/w/Saryn/Prime"}}})
SET = v2({"id": "set1", "items": [
    {"id": "p1", "slug": "saryn_prime_blueprint", "ducats": 45, "quantityInSet": 1,
     "i18n": {"en": {"name": "Saryn Prime Blueprint"}}},
    {"id": "set1", "slug": "saryn_prime_set", "setRoot": True, "i18n": {"en": {"name": "Saryn Prime Set"}}},
    {"id": "p2", "slug": "saryn_prime_systems_blueprint", "ducats": 100, "quantityInSet": 2,
     "i18n": {"en": {"name": "Saryn Prime Systems Blueprint"}}},
]})


def client(routes, **kw):
    session = RouteSession({"/v2/items": ITEMS, **routes})
    return WarframeMarketClient(session, min_interval=0, retry_delay=0, **kw), session


# ------------------------------------------------------------------ models
def test_parse_item_detail_reads_v2_fields_and_endo():
    d = parse_item_detail({"slug": "serration", "maxRank": 10, "baseEndo": 30, "endoMultiplier": 1,
                           "tradingTax": 2000, "i18n": {"en": {"name": "Serration", "wikiLink": "w"}}})
    assert d.item.max_rank == 10 and d.trading_tax == 2000 and d.wiki_url == "w"
    assert d.endo_at_max == 330
    assert parse_item_detail({}) is None


def test_parse_order_handles_missing_user_and_v1_names():
    o = parse_order({"id": "1", "order_type": "sell", "platinum": "12", "mod_rank": 3},
                    owner={"ingame_name": "Old", "status": "online"})
    assert (o.type, o.platinum, o.rank, o.seller, o.is_online) == ("sell", 12, 3, "Old", True)
    assert parse_order({"type": "sell"}) is None
    assert parse_order({"type": "sell", "platinum": 5, "visible": False}) is None


def test_parse_user_drops_unknown_activity():
    u = parse_user({"ingameName": "Tenno", "slug": "tenno", "status": "ingame", "reputation": 7,
                    "activity": {"type": "UNKNOWN"}, "avatar": "user/avatar.png"})
    assert u.url.endswith("/profile/tenno") and u.activity == "" and u.avatar_url.endswith("user/avatar.png")
    assert parse_user({}) is None


def test_weapon_auction_parse_and_price():
    raw = {"id": "a", "visible": True, "closed": False, "buyout_price": None, "starting_price": 100,
           "top_bid": 150, "owner": {"ingame_name": "X", "status": "online"},
           "item": {"type": "lich", "weapon_url_name": "kuva_bramma", "element": "Toxin", "damage": 58,
                    "having_ephemera": True}}
    a = parse_weapon_auction(raw)
    assert (a.element, a.damage, a.ephemera, asking_price(a)) == ("toxin", 58, True, 150)
    assert parse_weapon_auction({**raw, "item": {"type": "riven"}}) is None
    assert parse_weapon_auction({**raw, "closed": True}) is None


def test_disposition_dots():
    w = lambda d: RivenWeapon(item=Item("x", "X"), disposition=d).disposition_dots.count("●")
    assert [w(0.5), w(0.8), w(1.0), w(1.2), w(1.5)] == [1, 2, 3, 4, 5]
    assert RivenWeapon(item=Item("x", "X")).disposition_dots == "?"


# ------------------------------------------------------------------ client
def test_item_index_has_ids_ducats_and_lookups():
    c, _ = client({})
    names = run(c.item_names_by_id())
    assert names["p2"].ducats == 100 and names["set1"].is_set and names["set1"].vaulted
    assert run(c.find_exact("Saryn Prime")).slug == "saryn_prime_set"          # "<name> Set" match
    assert run(c.find_exact("Serration")).slug == "serration"
    assert run(c.find_exact("Saryn")) is None                                   # no fuzzy false positives


def test_get_item_and_set_sorted_root_first():
    c, s = client({"/v2/item/saryn_prime_systems_blueprint": DETAIL, "/set": SET})
    d = run(c.get_item("saryn_prime_systems_blueprint"))
    assert d.trading_tax == 6000 and d.rarity == "rare" and d.description == "Saryn systems."
    parts = run(c.get_set("saryn_prime_blueprint"))
    assert parts[0].set_root and [p.item.slug for p in parts[1:]] == ["saryn_prime_blueprint",
                                                                      "saryn_prime_systems_blueprint"]


def test_top_orders_passes_filters_and_sorts():
    top = v2({"sell": [order("1", "sell", 30), order("2", "sell", 20)],
              "buy": [order("3", "buy", 10), order("4", "buy", 15)]})
    c, s = client({"/top": top})
    best = run(c.get_top_orders("serration", rank=10, amber_stars=None, subtype="radiant"))
    assert [o.platinum for o in best["sell"]] == [20, 30]
    assert [o.platinum for o in best["buy"]] == [15, 10]
    url, params, headers = s.calls[-1]
    assert url.endswith("/v2/orders/item/serration/top")
    assert params == {"rank": 10, "subtype": "radiant"}
    assert headers["Platform"] == "pc" and headers["Crossplay"] == "true"


def test_recent_orders_and_user_orders_attach_owner():
    c, _ = client({"/v2/orders/recent": v2([order("1", "sell", 5)]),
                   "/v2/user/tenno": v2({**user("Tenno"), "slug": "tenno"}),
                   "/v2/orders/user/tenno": v2([{"id": "9", "type": "buy", "platinum": 7, "itemId": "m1"}])})
    assert run(c.get_recent_orders())[0].item_id == "p1"
    profile = run(c.get_user("Tenno"))
    mine = run(c.get_user_orders("Tenno", profile))
    assert mine[0].seller == "Tenno" and mine[0].status == "ingame" and mine[0].item_id == "m1"


def test_unknown_user_is_not_found():
    c, _ = client({})
    try:
        run(c.get_user("nobody"))
    except ItemNotFound:
        pass
    else:
        raise AssertionError("expected ItemNotFound")


def test_statistics_parsed_by_period():
    stats = {"payload": {"statistics_closed": {
        "48hours": [{"datetime": "2026-11-05T10:00:00", "volume": 2, "avg_price": 10, "median": 10}],
        "90days": [{"datetime": f"2026-09-{d:02d}T00:00:00", "volume": 1, "avg_price": d, "median": d,
                    "min_price": d - 1, "max_price": d + 1} for d in range(1, 21)]}}}
    c, _ = client({"/statistics": stats})
    got = run(c.get_statistics("serration"))
    assert len(got["48hours"]) == 1 and len(got["90days"]) == 20


def test_retries_once_on_429_then_succeeds():
    c, s = client({"/v2/orders/recent": [(429, {}), v2([order("1", "sell", 5)])]})
    assert len(run(c.get_recent_orders())) == 1
    assert sum(1 for u in s.urls() if u.endswith("/recent")) == 2


def test_404_is_not_retried():
    c, s = client({})
    try:
        run(c.get_item("nope"))
    except ItemNotFound:
        pass
    assert len(s.calls) == 1


def test_persistent_5xx_raises_market_error():
    c, _ = client({"/v2/orders/recent": (503, {})})
    try:
        run(c.get_recent_orders())
    except MarketError as exc:
        assert exc.status == 503 and not isinstance(exc, ItemNotFound)
    else:
        raise AssertionError("expected MarketError")


def test_catalogs_cached_and_weapon_auctions_query():
    weapons = v2([{"slug": "kuva_bramma", "i18n": {"en": {"name": "Kuva Bramma"}}},
                  {"slug": "kuva_kohm", "i18n": {"en": {"name": "Kuva Kohm"}}}])
    auctions = {"payload": {"auctions": [
        {"id": "a", "buyout_price": 300, "owner": {"ingame_name": "X", "status": "ingame"},
         "item": {"type": "lich", "weapon_url_name": "kuva_bramma", "element": "heat", "damage": 50}}]}}
    c, s = client({"/v2/lich/weapons": weapons, "/v1/auctions/search": auctions})
    assert run(c.resolve_weapon("lich", "bramma")).slug == "kuva_bramma"
    run(c.search_weapons("lich", "kohm"))
    assert sum(1 for u in s.urls() if u.endswith("/lich/weapons")) == 1
    got = run(c.get_weapon_auctions("lich", "kuva_bramma", element="heat", ephemera=True))
    assert got[0].damage == 50
    assert s.calls[-1][1] == {"type": "lich", "weapon_url_name": "kuva_bramma", "element": "heat",
                              "ephemera": "true"}


# ---------------------------------------------------------------- insights
def test_set_report_flip_profit_and_quantities():
    c, _ = client({"/set": SET})
    parts = run(c.get_set("saryn_prime_set"))
    orders = {
        "saryn_prime_set": [parse_order(order("s", "sell", 100)), parse_order(order("b", "buy", 80))],
        "saryn_prime_blueprint": [parse_order(order("1", "sell", 10)), parse_order(order("2", "buy", 8))],
        "saryn_prime_systems_blueprint": [parse_order(order("3", "sell", 20)),
                                          parse_order(order("4", "buy", 15)),
                                          parse_order(order("5", "sell", 5, status="offline"))],
    }
    r = set_report(parts, orders)
    assert r.parts_lowest_sell == 10 + 2 * 20                       # quantityInSet=2, offline ignored
    assert r.parts_highest_buy == 8 + 2 * 15
    assert r.flip_profit == 50 and "earns about 50p" in r.verdict
    assert r.total_ducats == 45 + 2 * 100


def test_set_report_missing_part_prices():
    c, _ = client({"/set": SET})
    parts = run(c.get_set("saryn_prime_set"))
    r = set_report(parts, {"saryn_prime_set": [parse_order(order("s", "sell", 100))]})
    assert r.flip_profit is None and "Not enough" in r.verdict


def _points(prices, rank=None):
    return [StatPoint(when=f"2026-08-{i + 1:02d}", volume=2, min_price=p - 1, max_price=p + 1,
                      avg_price=p, median=p, rank=rank) for i, p in enumerate(prices)]


def test_history_summary_trend_and_spark():
    pts = _points([10] * 7 + [20] * 7)
    h = history_summary(pts)
    assert h.volume == 28 and h.avg_price == 15 and h.change_pct == 100.0
    assert h.min_price == 9 and h.max_price == 21 and len(h.sparkline) == 14


def test_history_summary_rank_handling():
    pts = _points([5, 6], rank=0) + _points([50, 60], rank=10)
    assert history_summary(pts).avg_price == 5.5                    # defaults to lowest rank
    h = history_summary(pts, rank=10)
    assert h.avg_price == 55 and h.ranks == [0, 10]
    assert history_summary([]).points == 0


def test_sparkline_downsamples():
    h = history_summary(_points(list(range(1, 91))), spark_width=30)
    assert len(h.sparkline) == 30 and h.sparkline[0] == "▁" and h.sparkline[-1] == "█"
    assert sparkline([]) == "" and sparkline([3, 3]) == "▁▁"


def test_ducat_deals_best_value_first_online_only():
    c, _ = client({})
    names = run(c.item_names_by_id())
    recent = [parse_order(o) for o in (
        order("1", "sell", 10, "p1"), order("2", "sell", 6, "p1"),       # 45 ducats / 6p
        order("3", "sell", 5, "p2"),                                     # 100 ducats / 5p
        order("4", "sell", 1, "p2", status="offline"),
        order("5", "buy", 1, "p2"),
        order("6", "sell", 1, "set1"),                                   # sets have no ducat value here
        order("7", "sell", 1, "m1"))]                                    # no ducats at all
    deals = ducat_deals(recent, names, min_ducats=1)
    assert [(d.item.slug, d.platinum) for d in deals] == [("saryn_prime_systems_blueprint", 5),
                                                          ("saryn_prime_blueprint", 6)]
    assert ducat_deals(recent, names, min_ducats=50)[0].ducats == 100
    assert len(ducat_deals(recent, names, min_ducats=101)) == 0


def test_recent_trends_groups_by_item():
    c, _ = client({})
    names = run(c.item_names_by_id())
    recent = [parse_order(order(str(i), "sell" if i % 2 else "buy", 10 + i, "m1")) for i in range(5)]
    recent += [parse_order(order("x", "sell", 3, "p1"))]
    rows = recent_trends(recent, names, min_orders=2)
    assert len(rows) == 1 and rows[0].item.slug == "serration"
    assert (rows[0].orders, rows[0].sells, rows[0].buys) == (5, 2, 3)
    assert rows[0].avg_sell == 12 and rows[0].avg_buy == 12


def test_filter_weapon_auctions():
    def a(i, el, dmg, eph, price, status="ingame"):
        return parse_weapon_auction({"id": str(i), "buyout_price": price, "owner": {"ingame_name": "x",
                                     "status": status}, "item": {"type": "lich", "weapon_url_name": "w",
                                     "element": el, "damage": dmg, "having_ephemera": eph}})
    pool = [a(1, "heat", 60, True, 500), a(2, "heat", 30, False, 100), a(3, "toxin", 55, False, 200),
            a(4, "heat", 59, False, 50, status="offline"), a(5, "heat", 45, False, None)]
    assert [x.id for x in filter_weapon_auctions(pool, WeaponFilter(element="heat"))] == ["2", "1", "5"]
    assert [x.id for x in filter_weapon_auctions(pool, WeaponFilter(min_damage=50))] == ["3", "1"]
    assert [x.id for x in filter_weapon_auctions(pool, WeaponFilter(ephemera=True))] == ["1"]
    assert [x.id for x in filter_weapon_auctions(pool, WeaponFilter(max_price=150, online_only=False))] == ["4", "2"]
