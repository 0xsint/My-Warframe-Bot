"""FastAPI service exposing warframe.market data in a seller-friendly shape.

Run:      uvicorn api.main:app --reload
Docs:     http://127.0.0.1:8000/docs   (interactive Swagger UI)

It reuses market/client.py and market/analysis.py, so the API and the Discord
commands always show the same numbers. All upstream calls share one rate
limiter (warframe.market allows ~3 req/s) and a short cache, so hammering this
API does not hammer warframe.market.

Optional protection: set WFBOT_API_KEY and callers must send  X-API-Key: <key>.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Literal, Optional

import aiohttp
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from market.analysis import assess_price, build_report, top_orders, whisper
from market.client import ItemNotFound, WarframeMarketClient, MarketError

Platform = Literal["pc", "ps4", "xbox", "switch"]
Side = Literal["sell", "buy"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        app.state.market = WarframeMarketClient(session)
        yield


app = FastAPI(
    title="WarframeBOT Market API",
    description="Seller-focused views of warframe.market: pricing tiers, competition, "
                "buyers to whisper, and raw orders.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("WFBOT_CORS", "*").split(","),
                   allow_methods=["GET"], allow_headers=["*"])


def require_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    expected = os.getenv("WFBOT_API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")


def client() -> MarketClient:
    return app.state.market


def _handle(exc: MarketError) -> HTTPException:
    if isinstance(exc, ItemNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if exc.status == 429:
        return HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "5"})
    return HTTPException(status_code=502, detail=str(exc))


def _order_json(o, item_name: str) -> dict:
    d = asdict(o)
    d["online"] = o.is_online
    d["whisper"] = whisper(o, item_name)
    return d


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/v1/items/search", dependencies=[Depends(require_key)], summary="Find tradable items by name")
async def search_items(q: str = Query(..., min_length=1, max_length=80),
                       limit: int = Query(10, ge=1, le=50)):
    try:
        items = await client().search_items(q, limit)
    except MarketError as exc:
        raise _handle(exc)
    return {"query": q, "results": [
        {"slug": i.slug, "name": i.name, "image": i.image_url, "tags": list(i.tags),
         "max_rank": i.max_rank, "url": i.url} for i in items]}


@app.get("/v1/items/{slug}/report", dependencies=[Depends(require_key)],
         summary="Seller report: price tiers, competition, buyers, tips")
async def report(
    response: Response, slug: str,
    rank: Optional[int] = Query(None, ge=0, le=10, description="Mod/arcane rank filter"),
    platform: Platform = "pc",
    crossplay: bool = True,
    history: bool = Query(True, description="Include last-48h sales summary (best effort)"),
    my_price: Optional[int] = Query(None, ge=1, description="Where would this price rank?"),
):
    try:
        item = await client().resolve_item(slug)
        orders = await client().get_orders(item.slug, platform=platform, crossplay=crossplay)
        hist = await client().get_history(item.slug) if history else None
    except MarketError as exc:
        raise _handle(exc)
    rep = build_report(item.name, item.slug, orders, rank=rank, history=hist)
    data = rep.to_dict()
    data.update(platform=platform, crossplay=crossplay, image=item.image_url, url=item.url)
    if my_price is not None:
        data["my_price"] = assess_price(rep, my_price)
    response.headers["Cache-Control"] = "public, max-age=30"
    return data


@app.get("/v1/items/{slug}/orders", dependencies=[Depends(require_key)], summary="Raw orders, filtered")
async def orders(
    response: Response, slug: str,
    side: Literal["sell", "buy", "all"] = "all",
    online_only: bool = True,
    rank: Optional[int] = Query(None, ge=0, le=10),
    limit: int = Query(50, ge=1, le=500),
    platform: Platform = "pc",
    crossplay: bool = True,
):
    try:
        item = await client().resolve_item(slug)
        raw = await client().get_orders(item.slug, platform=platform, crossplay=crossplay)
    except MarketError as exc:
        raise _handle(exc)
    sides = ["sell", "buy"] if side == "all" else [side]
    picked = []
    for s in sides:
        picked += top_orders(raw, side=s, online_only=online_only, rank=rank, limit=limit)
    response.headers["Cache-Control"] = "public, max-age=30"
    return {"item": item.name, "slug": item.slug, "count": len(picked),
            "orders": [_order_json(o, item.name) for o in picked[:limit]]}


@app.get("/v1/items/{slug}/whispers", dependencies=[Depends(require_key)],
         summary="Ready-to-paste in-game whispers (buyers by default)")
async def whispers(
    slug: str,
    side: Side = Query("buy", description="'buy' = buyers you can sell to; 'sell' = cheapest sellers"),
    rank: Optional[int] = Query(None, ge=0, le=10),
    limit: int = Query(10, ge=1, le=50),
    platform: Platform = "pc",
    crossplay: bool = True,
):
    try:
        item = await client().resolve_item(slug)
        raw = await client().get_orders(item.slug, platform=platform, crossplay=crossplay)
    except MarketError as exc:
        raise _handle(exc)
    picks = top_orders(raw, side=side, rank=rank, limit=limit)
    return {"item": item.name, "side": side, "count": len(picks),
            "whispers": [{"seller": o.seller, "platinum": o.platinum, "rank": o.rank,
                          "status": o.status, "text": whisper(o, item.name)} for o in picks]}
