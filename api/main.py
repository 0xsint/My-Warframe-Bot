"""FastAPI service exposing warframe.market data in a trader-friendly shape.

Run:      uvicorn api.main:app --reload
Docs:     http://127.0.0.1:8000/docs   (interactive Swagger UI)

It reuses market/client.py, market/analysis.py and market/insights.py, so the
API and the Discord commands always show the same numbers. All upstream calls
share one rate limiter (warframe.market allows ~3 req/s) and a short cache.

Optional protection: set WFBOT_API_KEY and callers must send  X-API-Key: <key>.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Literal, Optional

import aiohttp
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from market.analysis import assess_price, build_report, top_orders, whisper
from market.client import ItemNotFound, MarketError, Order, WarframeMarketClient
from market.insights import (
    ELEMENTS,
    WeaponFilter,
    ducat_deals,
    filter_weapon_auctions,
    history_summary,
    recent_trends,
    set_report,
)
from market.models import asking_price
from market.riven import POLARITIES, RivenFilter, filter_rivens

Platform = Literal["pc", "ps4", "xbox", "switch", "mobile"]
Side = Literal["sell", "buy"]
Kind = Literal["lich", "sister"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        app.state.market = WarframeMarketClient(session)
        yield


def require_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    expected = os.getenv("WFBOT_API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")


app = FastAPI(
    title="WarframeBOT Market API",
    description="Trader views of warframe.market: pricing tiers, competition, buyers to whisper, "
                "sets, history, ducats, players and riven/lich/sister contracts.",
    version="2.0.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("WFBOT_CORS", "*").split(","),
                   allow_methods=["GET"], allow_headers=["*"])
v1 = APIRouter(prefix="/v1", dependencies=[Depends(require_key)])


def client(request: Request) -> WarframeMarketClient:
    return request.app.state.market


def _handle(exc: MarketError) -> HTTPException:
    if isinstance(exc, ItemNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if exc.status == 429:
        return HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "5"})
    return HTTPException(status_code=502, detail=str(exc))


def _order_json(o: Order, item_name: str) -> dict:
    d = asdict(o)
    d["online"] = o.is_online
    d["whisper"] = whisper(o, item_name)
    return d


def _cache(response: Response, seconds: int = 30) -> None:
    response.headers["Cache-Control"] = f"public, max-age={seconds}"


@app.get("/health")
async def health():
    return {"status": "ok"}


# ------------------------------------------------------------------- items
@v1.get("/items/search", summary="Find tradable items by name")
async def search_items(request: Request, q: str = Query(..., min_length=1, max_length=80),
                       limit: int = Query(10, ge=1, le=50)):
    try:
        items = await client(request).search_items(q, limit)
    except MarketError as exc:
        raise _handle(exc)
    return {"query": q, "results": [
        {"slug": i.slug, "name": i.name, "image": i.image_url, "tags": list(i.tags), "max_rank": i.max_rank,
         "ducats": i.ducats, "vaulted": i.vaulted, "url": i.url} for i in items]}


@v1.get("/items/{slug}", summary="Item facts: ducats, trading tax, mastery, rarity, endo, wiki link")
async def item_detail(request: Request, response: Response, slug: str):
    try:
        item = await client(request).resolve_item(slug)
        detail = await client(request).get_item(item.slug)
    except MarketError as exc:
        raise _handle(exc)
    _cache(response, 3600)
    d = asdict(detail)
    d.update(image=detail.item.image_url, url=detail.item.url, endo_at_max=detail.endo_at_max)
    return d


@v1.get("/items/{slug}/report", summary="Seller report: price tiers, competition, buyers, tips")
async def report(
    request: Request, response: Response, slug: str,
    rank: Optional[int] = Query(None, ge=0, le=10, description="Mod/arcane rank filter"),
    platform: Platform = "pc", crossplay: bool = True,
    history: bool = Query(True, description="Include last-48h sales summary (best effort)"),
    my_price: Optional[int] = Query(None, ge=1, description="Where would this price rank?"),
):
    c = client(request)
    try:
        item = await c.resolve_item(slug)
        orders = await c.get_orders(item.slug, platform=platform, crossplay=crossplay)
        hist = await c.get_history(item.slug) if history else None
    except MarketError as exc:
        raise _handle(exc)
    rep = build_report(item.name, item.slug, orders, rank=rank, history=hist)
    data = rep.to_dict()
    data.update(platform=platform, crossplay=crossplay, image=item.image_url, url=item.url)
    if my_price is not None:
        data["my_price"] = assess_price(rep, my_price)
    _cache(response)
    return data


@v1.get("/items/{slug}/orders", summary="Raw orders, filtered")
async def orders(
    request: Request, response: Response, slug: str,
    side: Literal["sell", "buy", "all"] = "all", online_only: bool = True,
    rank: Optional[int] = Query(None, ge=0, le=10), limit: int = Query(50, ge=1, le=500),
    platform: Platform = "pc", crossplay: bool = True,
):
    try:
        item = await client(request).resolve_item(slug)
        raw = await client(request).get_orders(item.slug, platform=platform, crossplay=crossplay)
    except MarketError as exc:
        raise _handle(exc)
    sides = ["sell", "buy"] if side == "all" else [side]
    picked = []
    for s in sides:
        picked += top_orders(raw, side=s, online_only=online_only, rank=rank, limit=limit)
    _cache(response)
    return {"item": item.name, "slug": item.slug, "count": len(picked[:limit]),
            "orders": [_order_json(o, item.name) for o in picked[:limit]]}


@v1.get("/items/{slug}/top", summary="Best 5 online sell and buy orders")
async def top(request: Request, response: Response, slug: str,
              rank: Optional[int] = Query(None, ge=0, le=10), charges: Optional[int] = Query(None, ge=0),
              subtype: Optional[str] = None, amber_stars: Optional[int] = Query(None, ge=0),
              cyan_stars: Optional[int] = Query(None, ge=0), platform: Platform = "pc"):
    try:
        item = await client(request).resolve_item(slug)
        best = await client(request).get_top_orders(item.slug, rank=rank, charges=charges, subtype=subtype,
                                                    amber_stars=amber_stars, cyan_stars=cyan_stars,
                                                    platform=platform)
    except MarketError as exc:
        raise _handle(exc)
    _cache(response)
    return {"item": item.name, "slug": item.slug,
            **{side: [_order_json(o, item.name) for o in best[side]] for side in ("sell", "buy")}}


@v1.get("/items/{slug}/whispers", summary="Ready-to-paste in-game whispers (buyers by default)")
async def whispers(request: Request, slug: str,
                   side: Side = Query("buy", description="'buy' = buyers you can sell to; 'sell' = cheapest sellers"),
                   rank: Optional[int] = Query(None, ge=0, le=10), limit: int = Query(10, ge=1, le=50),
                   platform: Platform = "pc", crossplay: bool = True):
    try:
        item = await client(request).resolve_item(slug)
        raw = await client(request).get_orders(item.slug, platform=platform, crossplay=crossplay)
    except MarketError as exc:
        raise _handle(exc)
    picks = top_orders(raw, side=side, rank=rank, limit=limit)
    return {"item": item.name, "side": side, "count": len(picks),
            "whispers": [{"seller": o.seller, "platinum": o.platinum, "rank": o.rank,
                          "status": o.status, "text": whisper(o, item.name)} for o in picks]}


@v1.get("/items/{slug}/set", summary="Set price vs the sum of its parts")
async def item_set(request: Request, response: Response, slug: str, platform: Platform = "pc"):
    c = client(request)
    try:
        item = await c.resolve_item(slug)
        parts = await c.get_set(item.slug)
        if len(parts) < 2:
            raise HTTPException(status_code=404, detail=f"{item.name} isn't part of a set")
        by_slug = {}
        for p in parts:
            best = await c.get_top_orders(p.item.slug, platform=platform)
            by_slug[p.item.slug] = best["sell"] + best["buy"]
    except MarketError as exc:
        raise _handle(exc)
    _cache(response, 60)
    return set_report(parts, by_slug).to_dict()


@v1.get("/items/{slug}/history", summary="90-day and 48-hour price history with trend")
async def history(request: Request, response: Response, slug: str,
                  rank: Optional[int] = Query(None, ge=0, le=10)):
    try:
        item = await client(request).resolve_item(slug)
        stats = await client(request).get_statistics(item.slug)
    except MarketError as exc:
        raise _handle(exc)
    _cache(response, 600)
    return {"item": item.name, "slug": item.slug,
            "days_90": history_summary(stats["90days"], period="90days", rank=rank).to_dict(),
            "hours_48": history_summary(stats["48hours"], period="48hours", rank=rank).to_dict(),
            "points_90": [asdict(p) for p in stats["90days"] if rank is None or p.rank == rank]}


# ---------------------------------------------------------------- activity
@v1.get("/recent", summary="Newest orders posted site-wide")
async def recent(request: Request, response: Response, side: Optional[Side] = None,
                 limit: int = Query(100, ge=1, le=500), platform: Platform = "pc"):
    try:
        raw = await client(request).get_recent_orders(platform=platform)
        names = await client(request).item_names_by_id()
    except MarketError as exc:
        raise _handle(exc)
    out = []
    for o in raw:
        item = names.get(o.item_id)
        if item and (side is None or o.type == side):
            out.append({**_order_json(o, item.name), "item": item.name, "slug": item.slug})
        if len(out) >= limit:
            break
    _cache(response, 60)
    return {"count": len(out), "orders": out}


@v1.get("/trends", summary="Busiest items among the newest orders")
async def trends(request: Request, response: Response, platform: Platform = "pc",
                 min_orders: int = Query(3, ge=1, le=50)):
    try:
        raw = await client(request).get_recent_orders(platform=platform)
        names = await client(request).item_names_by_id()
    except MarketError as exc:
        raise _handle(exc)
    _cache(response, 60)
    return {"trends": [{"item": r.item.name, "slug": r.item.slug, "orders": r.orders, "sells": r.sells,
                        "buys": r.buys, "quantity": r.quantity, "avg_sell": r.avg_sell, "avg_buy": r.avg_buy}
                       for r in recent_trends(raw, names, min_orders=min_orders)]}


@v1.get("/ducats/deals", summary="Best ducats-per-platinum prime parts on sale right now")
async def ducats(request: Request, response: Response, platform: Platform = "pc",
                 min_ducats: int = Query(45, ge=1, le=100), limit: int = Query(30, ge=1, le=100)):
    try:
        raw = await client(request).get_recent_orders(platform=platform)
        names = await client(request).item_names_by_id()
    except MarketError as exc:
        raise _handle(exc)
    _cache(response, 60)
    return {"deals": [{"item": d.item.name, "slug": d.item.slug, "ducats": d.ducats, "platinum": d.platinum,
                       "ducats_per_plat": round(d.ducats_per_plat, 2), "seller": d.seller}
                      for d in ducat_deals(raw, names, min_ducats=min_ducats, limit=limit)]}


# ------------------------------------------------------------------- users
@v1.get("/users/{name}", summary="A player's public profile and listings")
async def user(request: Request, response: Response, name: str):
    c = client(request)
    try:
        profile = await c.get_user(name)
        listed = await c.get_user_orders(name, profile)
        names = await c.item_names_by_id()
    except MarketError as exc:
        raise _handle(exc)
    _cache(response, 60)

    def row(o: Order) -> dict:
        item = names.get(o.item_id)
        label = item.name if item else "Unknown item"
        return {**_order_json(o, label), "item": label, "slug": item.slug if item else None}
    return {"profile": {**asdict(profile), "url": profile.url, "avatar_url": profile.avatar_url},
            "sell": [row(o) for o in listed if o.type == "sell"],
            "buy": [row(o) for o in listed if o.type == "buy"]}


# --------------------------------------------------------------- contracts
@v1.get("/rivens/{weapon}", summary="Riven contracts for a weapon, filtered")
async def rivens(request: Request, response: Response, weapon: str,
                 positive: list[str] = Query(default=[], description="Wanted positive stats (repeat)"),
                 negative: Optional[str] = None, polarity: Optional[Literal[POLARITIES]] = None,
                 rank: Optional[int] = Query(None, ge=0, le=8), max_price: Optional[int] = Query(None, ge=1),
                 online_only: bool = True, platform: Platform = "pc"):
    c = client(request)
    try:
        item = await c.resolve_riven_weapon(weapon)
        auctions = await c.get_riven_auctions(item.slug, platform=platform)
        info = await c.riven_weapon_info(item.slug)
    except MarketError as exc:
        raise _handle(exc)
    kept = filter_rivens(auctions, RivenFilter(polarity=polarity, rank=rank, positives=tuple(positive),
                                               negative=negative, max_price=max_price, online_only=online_only))
    _cache(response)
    return {"weapon": item.name, "slug": item.slug,
            "disposition": info.disposition if info else None,
            "count": len(kept),
            "auctions": [{**asdict(a), "price": asking_price(a), "url": a.url} for a in kept]}


@v1.get("/{kind}/{weapon}", summary="Kuva Lich / Sister of Parvos weapon contracts")
async def weapon_contracts(request: Request, response: Response, kind: Kind, weapon: str,
                           element: Optional[Literal[ELEMENTS]] = None,
                           min_damage: Optional[int] = Query(None, ge=25, le=60), ephemera: Optional[bool] = None,
                           max_price: Optional[int] = Query(None, ge=1), online_only: bool = True,
                           platform: Platform = "pc"):
    c = client(request)
    try:
        entry = await c.resolve_weapon(kind, weapon)
        auctions = await c.get_weapon_auctions(kind, entry.slug, element=element, ephemera=ephemera,
                                               platform=platform)
    except MarketError as exc:
        raise _handle(exc)
    kept = filter_weapon_auctions(auctions, WeaponFilter(element=element, min_damage=min_damage,
                                                         ephemera=ephemera, max_price=max_price,
                                                         online_only=online_only))
    _cache(response)
    return {"weapon": entry.name, "slug": entry.slug, "kind": kind, "count": len(kept),
            "auctions": [{**asdict(a), "price": asking_price(a), "url": a.url} for a in kept]}


app.include_router(v1)
