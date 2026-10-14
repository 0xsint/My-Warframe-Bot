from market.analysis import (assess_price, build_report, percentile, top_orders, whisper)
from market.client import Order, _parse_item, _parse_order


def o(kind, plat, status="ingame", rank=None, name="Seller", rep=0):
    return Order(id="x", type=kind, platinum=plat, quantity=1, rank=rank,
                 seller=name, status=status, reputation=rep)


def test_percentile_basics():
    assert percentile([], 50) is None
    assert percentile([7], 25) == 7.0
    assert percentile([10, 20, 30, 40], 50) == 25.0
    assert percentile([10, 20, 30, 40], 0) == 10 and percentile([10, 20, 30, 40], 100) == 40


def test_online_only_headline_numbers_and_tiers():
    orders = [o("sell", p) for p in (20, 22, 25, 30, 40)] + [o("sell", 5, status="offline")]
    orders += [o("buy", 15), o("buy", 12), o("buy", 50, status="offline")]
    r = build_report("Mirage Prime Set", "mirage_prime_set", orders)
    assert r.online_sellers == 5 and r.total_sell_orders == 6
    assert r.lowest_sell == 20                 # the 5p offline order is ignored
    assert r.highest_buy == 15                 # the 50p offline buy is ignored
    assert r.spread == 5
    assert r.fast_price == 19                  # undercut cheapest by 1
    assert r.fast_price <= r.balanced_price <= r.patient_price
    assert r.quick_sell_price == 15


def test_outlier_lowball_is_not_used_as_reference():
    orders = [o("sell", p) for p in (3, 20, 21, 22, 25)]
    r = build_report("X", "x", orders)
    assert r.outlier_low and r.lowest_sell == 3
    assert r.fast_price == 19                  # based on 20, not 3
    assert any("far below" in t for t in r.tips)


def test_small_gap_is_not_flagged_as_outlier():
    r = build_report("X", "x", [o("sell", p) for p in (18, 20, 21, 22)])
    assert not r.outlier_low and r.fast_price == 17


def test_crossed_market_never_lists_below_buyer_and_tips_instant_sale():
    orders = [o("sell", 10), o("sell", 12), o("buy", 14)]
    r = build_report("X", "x", orders)
    assert r.fast_price >= 14
    assert any("instant sale" in t for t in r.tips)


def test_no_online_sellers_falls_back_to_offline_reference():
    r = build_report("X", "x", [o("sell", 30, status="offline"), o("sell", 40, status="offline")])
    assert r.fast_price is None and r.offline_median_sell == 35
    assert any("offline" in t.lower() for t in r.tips)


def test_empty_market():
    r = build_report("X", "x", [])
    assert r.lowest_sell is None and "Dead market" in r.market_tone
    r.to_dict()  # must be serialisable


def test_never_recommends_below_one_platinum():
    r = build_report("X", "x", [o("sell", 1), o("sell", 1), o("sell", 1)])
    assert r.fast_price >= 1


def test_rank_filter_and_mixed_rank_warning():
    orders = [o("sell", 10, rank=0), o("sell", 12, rank=0), o("sell", 90, rank=10), o("sell", 95, rank=10)]
    mixed = build_report("Mod", "mod", orders)
    assert mixed.mixed_ranks and any("rank" in t for t in mixed.tips)
    assert [(row.rank, row.lowest_sell) for row in mixed.rank_table] == [(0, 10), (10, 90)]
    maxed = build_report("Mod", "mod", orders, rank=10)
    assert not maxed.mixed_ranks and maxed.lowest_sell == 90


def test_ladder_groups_prices_and_is_capped():
    r = build_report("X", "x", [o("sell", p) for p in (5, 5, 5, 6, 7, 8, 9, 10, 11)], ladder_size=4)
    assert [(l.price, l.orders) for l in r.price_ladder] == [(5, 3), (6, 1), (7, 1), (8, 1)]


def test_tone():
    many_sellers = build_report("X", "x", [o("sell", 10 + i) for i in range(10)] + [o("buy", 5)])
    assert "Buyer's market" in many_sellers.market_tone
    hot = build_report("X", "x", [o("sell", 10)] + [o("buy", 5 + i) for i in range(3)])
    assert "Seller's market" in hot.market_tone


def test_history_tip_when_asks_overshoot_recent_sales():
    hist = {"volume_48h": 30, "avg_price_48h": 20.0, "median_48h": 20.0}
    r = build_report("X", "x", [o("sell", p) for p in (40, 42, 44)], history=hist)
    assert any("overshooting" in t for t in r.tips)


def test_assess_price_positions():
    r = build_report("X", "x", [o("sell", p) for p in (20, 22, 22, 30)])
    a = assess_price(r, 22)
    assert a["sellers_cheaper"] == 1 and a["sellers_tied"] == 2
    assert assess_price(r, 19)["verdict"] == "Priced to sell fast."
    assert "Above" in assess_price(r, 200)["verdict"]
    assert assess_price(build_report("X", "x", []), 10)["verdict"].startswith("No online")


def test_whisper_matches_site_format_and_flips_verb():
    sell = o("sell", 25, name="Tenno1", rank=3)
    buy = o("buy", 40, name="Tenno2")
    assert whisper(sell, "Serration") == (
        '/w Tenno1 Hi! I want to buy: "Serration" (rank 3) for 25 platinum. (warframe.market)')
    assert whisper(buy, "Mirage Prime Set") == (
        '/w Tenno2 Hi! I want to sell: "Mirage Prime Set" for 40 platinum. (warframe.market)')


def test_top_orders_sorting_and_filters():
    orders = [o("buy", 10), o("buy", 30), o("buy", 20, status="offline"), o("buy", 25, rep=5), o("sell", 5)]
    best = top_orders(orders, side="buy", limit=2)
    assert [b.platinum for b in best] == [30, 25]                 # offline excluded, high first
    assert [b.platinum for b in top_orders(orders, side="buy", online_only=False, limit=1)] == [30]
    cheapest = top_orders([o("sell", 9), o("sell", 3)], side="sell")
    assert cheapest[0].platinum == 3


def test_parse_order_defensive():
    good = {"id": "1", "type": "sell", "platinum": 15, "quantity": 2, "rank": 0, "visible": True,
            "user": {"ingameName": "Bob", "status": "online", "reputation": 3, "platform": "pc",
                     "crossplay": True}}
    p = _parse_order(good)
    assert p and p.seller == "Bob" and p.is_online and p.quantity == 2 and p.rank == 0
    assert _parse_order({"type": "sell"}) is None                # no platinum
    assert _parse_order({**good, "visible": False}) is None      # hidden listing
    assert _parse_order({"type": "buy", "platinum": 5}).seller == "unknown"   # no user block


def test_parse_item_defensive():
    it = _parse_item({"slug": "mirage_prime_set", "tags": ["set"],
                      "i18n": {"en": {"name": "Mirage Prime Set", "icon": "items/images/en/a.png"}}})
    assert it.name == "Mirage Prime Set"
    assert it.image_url == "https://warframe.market/static/assets/items/images/en/a.png"
    assert _parse_item({"i18n": {}}) is None
    assert _parse_item({"slug": "x"}).name == "x"
