"""FastAPI endpoints against a fake upstream (no network)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from api import main as api  # noqa: E402
from fakes import ITEMS, RouteSession, order, v2  # noqa: E402
from market.client import WarframeMarketClient  # noqa: E402

ROUTES = {
    "/v2/items": ITEMS,
    "/v2/orders/item/serration": v2([order("1", "sell", 30, "m1", rank=10), order("2", "buy", 20, "m1", rank=10)]),
    "/v2/orders/item/serration/top": v2({"sell": [order("1", "sell", 30, "m1")], "buy": []}),
    "/v2/orders/recent": v2([order("3", "sell", 5, "p2"), order("4", "buy", 3, "m1")]),
    "/v2/item/serration": v2({"id": "m1", "slug": "serration", "maxRank": 10, "tradingTax": 2000,
                              "i18n": {"en": {"name": "Serration"}}}),
}


@pytest.fixture()
def http(monkeypatch):
    monkeypatch.delenv("WFBOT_API_KEY", raising=False)
    with TestClient(api.app) as c:
        api.app.state.market = WarframeMarketClient(RouteSession(dict(ROUTES)), min_interval=0, retry_delay=0)
        yield c


def test_health_and_search(http):
    assert http.get("/health").json() == {"status": "ok"}
    r = http.get("/v1/items/search", params={"q": "serr"}).json()
    assert r["results"][0]["slug"] == "serration"


def test_item_report_top_and_detail(http):
    rep = http.get("/v1/items/serration/report", params={"rank": 10, "history": False, "my_price": 25}).json()
    assert rep["lowest_sell"] == 30 and rep["highest_buy"] == 20 and "verdict" in rep["my_price"]
    top = http.get("/v1/items/serration/top").json()
    assert top["sell"][0]["whisper"].startswith("/w T1") and top["buy"] == []
    detail = http.get("/v1/items/serration").json()
    assert detail["trading_tax"] == 2000


def test_recent_trends_and_ducats(http):
    recent = http.get("/v1/recent", params={"side": "sell"}).json()
    assert recent["count"] == 1 and recent["orders"][0]["item"] == "Saryn Prime Systems Blueprint"
    assert http.get("/v1/trends", params={"min_orders": 1}).json()["trends"]
    deals = http.get("/v1/ducats/deals").json()["deals"]
    assert deals[0]["ducats_per_plat"] == 20.0


def test_not_found_and_api_key(http, monkeypatch):
    assert http.get("/v1/users/nobody").status_code == 404
    monkeypatch.setenv("WFBOT_API_KEY", "k")
    assert http.get("/v1/recent").status_code == 401
    assert http.get("/v1/recent", headers={"X-API-Key": "k"}).status_code == 200
    assert http.get("/health").status_code == 200
