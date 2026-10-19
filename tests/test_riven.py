import asyncio

import _stubs  # noqa: E402
_stubs.install()

from market.client import (  # noqa: E402
    RivenAttribute, RivenAuction, WarframeMarketClient, _parse_riven_auction,
)
from market.riven import (  # noqa: E402
    RivenFilter, asking_price, filter_rivens, has_negative, stat_label, stat_suggestions, unknown_stats,
)


def raw(*, id="1", status="ingame", buyout=None, bid=None, start=None, rank=8,
        polarity="madurai", rerolls=3, attrs=None, visible=True, closed=False, item_type="riven"):
    attrs = attrs if attrs is not None else [
        {"url_name": "critical_damage", "value": 120.0, "positive": True},
        {"url_name": "multishot", "value": 80.0, "positive": True},
        {"url_name": "recoil", "value": -30.0, "positive": False},
    ]
    return {
        "id": id, "visible": visible, "closed": closed, "platform": "pc",
        "buyout_price": buyout, "starting_price": start, "top_bid": bid,
        "owner": {"ingame_name": f"seller{id}", "status": status},
        "item": {"type": item_type, "weapon_url_name": "acceltra", "name": "sati-acritox",
                 "mod_rank": rank, "re_rolls": rerolls, "polarity": polarity,
                 "mastery_level": 16, "attributes": attrs},
    }


def riven(**kw) -> RivenAuction:
    return _parse_riven_auction(raw(**kw))


SEARCH = {"payload": {"auctions": [
    raw(id="a", buyout=500),
    raw(id="b", item_type="mod"),                 # not a riven, dropped
    raw(id="c", visible=False, buyout=10),        # hidden, dropped
    raw(id="d", closed=True, buyout=20),          # closed, dropped
]}}

RIVEN_WEAPONS = {"data": [
    {"slug": "kuva_bramma", "i18n": {"en": {"name": "Kuva Bramma", "icon": "k.png", "thumb": "k_t.png"}}},
    {"slug": "coda_bassocyst", "i18n": {"en": {"name": "Coda Bassocyst"}}},
    {"slug": "kuva_ayanga", "i18n": {"en": {"name": "Kuva Ayanga"}}},
    {"slug": "acceltra", "i18n": {"en": {"name": "Acceltra"}}},
]}

RIVEN_ATTRIBUTES = {"data": [
    {"slug": "critical_damage", "i18n": {"en": {"name": "Critical Damage"}}},
    {"slug": "slash_damage", "i18n": {"en": {"name": "Slash"}}},
    {"slug": "recoil", "i18n": {"en": {"name": "Recoil"}}},
    {"slug": "base_damage_/_melee_damage", "i18n": {"en": {"name": "Base Damage"}}},
]}


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
        self.calls.append((url, params))
        if "/auctions/search" in url:
            return FakeResp(200, SEARCH)
        if url.endswith("/v2/riven/weapons"):
            return FakeResp(200, RIVEN_WEAPONS)
        if url.endswith("/v2/riven/attributes"):
            return FakeResp(200, RIVEN_ATTRIBUTES)
        return FakeResp(404, {})


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---- parsing and client ----------------------------------------------------

def test_parse_drops_non_riven_hidden_and_closed():
    assert _parse_riven_auction(raw(item_type="mod")) is None
    assert _parse_riven_auction(raw(visible=False)) is None
    assert _parse_riven_auction(raw(closed=True)) is None
    assert _parse_riven_auction(raw(buyout=100)) is not None


def test_parse_fields():
    a = riven(buyout=750, status="online")
    assert a.weapon == "acceltra" and a.name == "sati-acritox"
    assert a.mod_rank == 8 and a.re_rolls == 3 and a.polarity == "madurai"
    assert a.buyout_price == 750 and a.seller == "seller1" and a.is_online
    assert a.attributes[0] == RivenAttribute("critical_damage", 120.0, True)
    assert a.attributes[2].positive is False


def test_client_get_riven_auctions_uses_v1_and_drops_bad_entries():
    s = FakeSession()
    c = WarframeMarketClient(s)
    auctions = run(c.get_riven_auctions("acceltra"))
    assert [a.id for a in auctions] == ["a"]
    url, params = s.calls[0]
    assert "/v1/auctions/search" in url
    assert params == {"type": "riven", "weapon_url_name": "acceltra"}


# ---- filtering -------------------------------------------------------------

def test_asking_price_prefers_buyout_then_bid_then_start():
    assert asking_price(riven(buyout=100, bid=50, start=10)) == 100
    assert asking_price(riven(bid=50, start=10)) == 50
    assert asking_price(riven(start=10)) == 10
    assert asking_price(riven()) is None


def test_filter_polarity_rank_and_price():
    rows = [
        riven(id="1", buyout=100, polarity="madurai", rank=8),
        riven(id="2", buyout=200, polarity="vazarin", rank=8),
        riven(id="3", buyout=300, polarity="madurai", rank=5),
        riven(id="4", buyout=900, polarity="madurai", rank=8),
    ]
    out = filter_rivens(rows, RivenFilter(polarity="madurai", rank=8, max_price=500))
    assert [a.id for a in out] == ["1"]


def test_filter_positive_stats_match_by_prefix_and_ignore_negative():
    rows = [
        riven(id="1", buyout=100),                               # crit dmg + multishot
        riven(id="2", buyout=100, attrs=[
            {"url_name": "critical_damage", "value": 90.0, "positive": True}]),
        riven(id="3", buyout=100, attrs=[
            {"url_name": "recoil", "value": 40.0, "positive": False},   # negative, not a buff
            {"url_name": "critical_damage", "value": 90.0, "positive": True}]),
    ]
    out = filter_rivens(rows, RivenFilter(positives=("crit damage", "multi")))
    assert [a.id for a in out] == ["1"]
    assert [a.id for a in filter_rivens(rows, RivenFilter(positives=("recoil",)))] == []


def test_filter_online_only_default_and_off():
    rows = [riven(id="1", buyout=100, status="offline"), riven(id="2", buyout=200, status="ingame")]
    assert [a.id for a in filter_rivens(rows, RivenFilter())] == ["2"]
    assert [a.id for a in filter_rivens(rows, RivenFilter(online_only=False))] == ["1", "2"]


def test_filter_sorts_cheapest_first_with_unpriced_last():
    rows = [
        riven(id="none"),
        riven(id="expensive", buyout=900),
        riven(id="cheap", buyout=50),
        riven(id="bid", bid=300),
    ]
    out = filter_rivens(rows, RivenFilter(online_only=False))
    assert [a.id for a in out] == ["cheap", "bid", "expensive", "none"]


def test_unknown_stats_flags_typos_only():
    rows = [riven()]
    assert unknown_stats(rows, ["crit damage", "multishot", "toxn"]) == ["toxn"]
    assert unknown_stats([], ["anything"]) == []


def test_filter_negative_stat():
    rows = [
        riven(id="1", buyout=100),                                     # recoil negative (default)
        riven(id="2", buyout=100, attrs=[
            {"url_name": "critical_damage", "value": 90.0, "positive": True},
            {"url_name": "zoom", "value": -20.0, "positive": False}]),
    ]
    assert [a.id for a in filter_rivens(rows, RivenFilter(negative="recoil"))] == ["1"]
    assert [a.id for a in filter_rivens(rows, RivenFilter(negative="zoom"))] == ["2"]
    assert [a.id for a in filter_rivens(rows, RivenFilter(negative="multishot"))] == []


def test_has_negative_ignores_positive_stats():
    a = riven()   # crit damage and multishot positive, recoil negative
    assert has_negative(a, "recoil")
    assert not has_negative(a, "critical damage")


def test_stat_label_and_suggestions():
    assert stat_label("critical_damage") == "Critical Damage"
    assert stat_label("base_damage_/_melee_damage") == "Base Damage / Melee Damage"
    slugs = ["critical_damage", "critical_chance", "slash_damage", "recoil", "multishot"]
    assert stat_suggestions("crit", slugs) == ["Critical Chance", "Critical Damage"]
    assert stat_suggestions("damage", slugs) == ["Critical Damage", "Slash Damage"]
    assert stat_suggestions("", slugs, limit=2) == ["Critical Chance", "Critical Damage"]
    assert stat_suggestions("zzz", slugs) == []


def test_resolve_riven_weapon_finds_kuva_and_coda():
    c = WarframeMarketClient(FakeSession())
    assert run(c.resolve_riven_weapon("kuva bramma")).slug == "kuva_bramma"
    assert run(c.resolve_riven_weapon("bassocyst")).slug == "coda_bassocyst"
    try:
        run(c.resolve_riven_weapon("not a weapon xyz"))
    except Exception as exc:
        assert type(exc).__name__ == "ItemNotFound"
    else:
        raise AssertionError("expected ItemNotFound")


def test_get_riven_attributes_returns_slugs():
    c = WarframeMarketClient(FakeSession())
    slugs = run(c.get_riven_attributes())
    assert "critical_damage" in slugs and "recoil" in slugs


def test_riven_weapon_game_family_names_resolve():
    c = WarframeMarketClient(FakeSession())
    assert run(c.resolve_riven_weapon("Sister of Parvos")).slug == "coda_bassocyst"
    assert run(c.resolve_riven_weapon("kuva lich bramma")).slug == "kuva_bramma"


def test_resolve_riven_weapons_returns_whole_family():
    c = WarframeMarketClient(FakeSession())
    kuva = run(c.resolve_riven_weapons("kuva lich"))
    assert {w.slug for w in kuva} == {"kuva_bramma", "kuva_ayanga"}
    assert [w.slug for w in run(c.resolve_riven_weapons("bassocyst"))] == ["coda_bassocyst"]
