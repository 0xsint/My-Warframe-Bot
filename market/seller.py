"""warframe.market *write* access: your own listings and online status.

Kept apart from market/client.py (public, read-only) on purpose: this class
owns its own aiohttp session + cookie jar so the CSRF token and your JWT never
mix with the shared session.

Auth, in order of preference (set in .env):
    WFM_JWT                      token copied from your browser's "JWT" cookie
    WFM_EMAIL + WFM_PASSWORD     password sign-in (may be blocked by Cloudflare)

Tokens are never written to the log.

CSRF handling: a token is looked for in the site's HTML (<meta name="csrf-token">
or an inline csrf_token value) and in any csrf* cookie, sent as X-CSRFToken
with Origin/Referer headers, and re-fetched once if the server rejects it.
"""
from __future__ import annotations

import asyncio
import json as jsonlib
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp
from yarl import URL

from .client import Item, WarframeMarketClient

log = logging.getLogger("warframebot.market.seller")

API_V1 = "https://api.warframe.market/v1"
API_V2 = "https://api.warframe.market/v2"
SITE = "https://warframe.market"
WS_URL = "wss://ws.warframe.market/socket"
USER_AGENT = "WarframeBOT/2.0 (personal listing tool)"
MIN_GAP = 0.4                      # warframe.market allows roughly 3 requests/second
STATUSES = ("ingame", "online", "invisible")

_CSRF_PATTERNS = [
    re.compile(r'<meta[^>]+name=["\']csrf[-_]?token["\'][^>]*content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*name=["\']csrf[-_]?token["\']', re.I),
    re.compile(r'["\']csrf[-_]?token["\']\s*[:=]\s*["\']([^"\']+)["\']', re.I),
]


class SellerError(Exception):
    """Anything that goes wrong while signing in or changing your listings."""


@dataclass
class ListedOrder:
    id: str
    item_name: str
    order_type: str
    platinum: int
    quantity: int
    rank: Optional[int] = None
    perTrade: Optional[int] = None
    mod_charges: Optional[int] = None
    subtype: Optional[str] = None
    amberStars: Optional[int] = None
    cyanStars: Optional[int] = None
    visible: bool = True
    item_slug: str = ""


def _listed(raw: dict[str, Any], item: Optional[Item]) -> ListedOrder:
    return ListedOrder(
        id=str(raw.get("id") or "?"),
        item_name=item.name if item else str(raw.get("itemId") or "Unknown item"),
        item_slug=item.slug if item else "",
        order_type=str(raw.get("type") or "?"),
        platinum=int(raw.get("platinum") or 0),
        quantity=int(raw["quantity"]) if raw.get("quantity") is not None else 1,
        rank=raw.get("rank"),
        perTrade=raw.get("perTrade"),
        mod_charges=raw.get("charges"),
        subtype=raw.get("subtype"),
        amberStars=raw.get("amberStars"),
        cyanStars=raw.get("cyanStars"),
        visible=bool(raw.get("visible", True)),
    )


class MarketSeller:
    """``market`` is the shared public client, used to turn item ids into names
    without extra API calls."""

    def __init__(self, market: WarframeMarketClient) -> None:
        self.market = market
        self._session: aiohttp.ClientSession | None = None
        self._jwt: str | None = os.getenv("WFM_JWT") or None
        self._static_token = self._jwt is not None      # a pasted token can't be refreshed
        self._csrf: str | None = None
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._status_task: asyncio.Task | None = None

    # ------------------------------------------------------------ plumbing
    @property
    def configured(self) -> bool:
        return bool(self._jwt or (os.getenv("WFM_EMAIL") and os.getenv("WFM_PASSWORD")))

    def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20),
                                                  cookie_jar=aiohttp.CookieJar())
        return self._session

    async def close(self) -> None:
        if self._status_task and not self._status_task.done():
            self._status_task.cancel()
        if self._session and not self._session.closed:
            await self._session.close()

    def _headers(self, platform: str = "pc", *, signin: bool = False) -> dict[str, str]:
        h = {
            "User-Agent": USER_AGENT, "Accept": "application/json",
            "Content-Type": "application/json", "Language": "en", "Platform": platform,
            "Origin": SITE, "Referer": SITE + "/",
        }
        if signin:
            h["Authorization"] = "JWT"                   # sign-in uses a bare "JWT" header
        elif self._jwt:
            h["Authorization"] = f"JWT {self._jwt}"
        if self._csrf:
            h["X-CSRFToken"] = self._csrf
        return h

    def _cookie(self, needle: str) -> str | None:
        jar = self._sess().cookie_jar
        for url in (URL(SITE), URL(API_V1)):
            for name, morsel in jar.filter_cookies(url).items():
                if needle in name.lower():
                    return morsel.value
        return None

    async def _refresh_csrf(self) -> None:
        html = ""
        try:
            async with self._sess().get(SITE + "/", headers={"User-Agent": USER_AGENT,
                                                             "Accept": "text/html"}) as r:
                html = await r.text()
        except aiohttp.ClientError as exc:
            log.warning("Could not load %s to look for a CSRF token: %s", SITE, exc)
        token = next((m.group(1) for p in _CSRF_PATTERNS if (m := p.search(html))), None)
        self._csrf = token or self._cookie("csrf")
        log.info("CSRF token %s", "found" if self._csrf else "NOT found")

    async def _send(self, method: str, url: str, *, body=None, platform="pc", signin=False):
        async with self._lock:
            gap = MIN_GAP - (time.monotonic() - self._last)
            if gap > 0:
                await asyncio.sleep(gap)
            cookies = {"JWT": self._jwt} if self._jwt and not signin else None
            try:
                async with self._sess().request(method, url, json=body, cookies=cookies,
                                                headers=self._headers(platform, signin=signin)) as r:
                    text = await r.text()
                    status, headers = r.status, r.headers
            except aiohttp.ClientError as exc:
                raise SellerError(f"network error: {exc}") from exc
            except asyncio.TimeoutError as exc:
                raise SellerError("warframe.market timed out") from exc
            finally:
                self._last = time.monotonic()
        try:
            data = jsonlib.loads(text) if text else {}
        except ValueError:
            data = {"raw": text[:200]}                  # e.g. a Cloudflare challenge page
        return status, headers, data

    @staticmethod
    def _short(data) -> str:
        return str(data)[:300]

    async def login(self) -> str:
        email, password = os.getenv("WFM_EMAIL"), os.getenv("WFM_PASSWORD")
        if not (email and password):
            raise SellerError("Set WFM_JWT, or WFM_EMAIL and WFM_PASSWORD, in .env")
        if self._csrf is None:
            await self._refresh_csrf()
        body = {"auth_type": "header", "email": email, "password": password, "device_id": "warframebot"}
        for attempt in range(2):
            status, headers, data = await self._send("POST", API_V1 + "/auth/signin", body=body, signin=True)
            if status < 400:
                break
            if attempt == 0 and "csrf" in str(data).lower():
                await self._refresh_csrf()
                continue
            raise SellerError(f"sign-in failed ({status}): {self._short(data)}")
        token = headers.get("Authorization", "")
        if token.upper().startswith("JWT "):
            token = token[4:]
        token = token or self._cookie("JWT") or ""
        if not token:
            raise SellerError("sign-in succeeded but no JWT was returned")
        log.info("Signed in to warframe.market")
        return token

    async def _call(self, method: str, path: str, *, body=None, platform="pc") -> dict:
        """An authenticated v2 call, with one CSRF retry and one re-login on 401."""
        if self._jwt is None:
            self._jwt = await self.login()
        csrf_retried = relogged = False
        while True:
            status, _, data = await self._send(method, API_V2 + path, body=body, platform=platform)
            if status < 400:
                return data if isinstance(data, dict) else {}
            if "csrf" in str(data).lower() and not csrf_retried:
                csrf_retried = True
                await self._refresh_csrf()
                continue
            if status == 401 and not self._static_token and not relogged:
                relogged = True
                self._jwt = await self.login()
                continue
            if status == 401:
                raise SellerError("warframe.market rejected the token (401). Copy a fresh JWT cookie into WFM_JWT.")
            raise SellerError(f"{status}: {self._short(data)}")

    async def _item_for(self, item_id: str) -> Optional[Item]:
        try:
            return await self.market.item_by_id(item_id)
        except Exception:
            return None

    # -------------------------------------------------------------- orders
    async def create_order(self, item: Item, *, order_type: str, platinum: int, quantity: int = 1,
                           rank: int | None = None, per_trade: int | None = None,
                           charges: int | None = None, subtype: str | None = None,
                           amber_stars: int | None = None, cyan_stars: int | None = None,
                           visible: bool = True, platform: str = "pc") -> ListedOrder:
        if not item.id:
            raise SellerError(f"warframe.market didn't give an id for {item.name}; try again later.")
        if item.max_rank is not None:
            if rank is None:
                raise SellerError(f"{item.name} is rankable - pass a rank between 0 and {item.max_rank}.")
            if rank > item.max_rank:
                raise SellerError(f"{item.name} only goes up to rank {item.max_rank}.")
        else:
            rank = None
        if item.subtypes and subtype and subtype not in item.subtypes:
            raise SellerError(f"{item.name} subtypes are: {', '.join(item.subtypes)}.")
        body: dict[str, Any] = {"itemId": item.id, "type": order_type, "platinum": platinum,
                                "quantity": quantity, "visible": visible}
        for key, value in (("rank", rank), ("perTrade", per_trade), ("charges", charges),
                           ("subtype", subtype), ("amberStars", amber_stars), ("cyanStars", cyan_stars)):
            if value is not None:
                body[key] = value
        data = await self._call("POST", "/order", body=body, platform=platform)
        order = data.get("data") or {}
        log.info("Order created: %s %s x%s @ %sp", order_type, item.slug, quantity, platinum)
        return _listed({**body, **order}, item)

    async def update_order(self, order_id: str, *, platform: str = "pc", **changes) -> ListedOrder:
        """``changes`` use the API's own keys: type, platinum, quantity, perTrade,
        visible, charges, rank, subtype, amberStars, cyanStars. None values are skipped."""
        body = {k: v for k, v in changes.items() if v is not None}
        if not body:
            raise SellerError("Nothing to change: pass at least one field.")
        data = await self._call("PATCH", f"/order/{order_id}", body=body, platform=platform)
        order = data.get("data") or {}
        log.info("Order updated: %s (%s)", order_id, ", ".join(body))
        return _listed(order, await self._item_for(str(order.get("itemId") or "")))

    async def close_order(self, order_id: str, quantity: int | None = None, *,
                          platform: str = "pc") -> ListedOrder:
        """Mark (some of) an order as sold/bought. It then counts in the site's statistics."""
        body = {"quantity": quantity} if quantity is not None else {}
        data = await self._call("POST", f"/order/{order_id}/close", body=body, platform=platform)
        order = data.get("data") or {}
        log.info("Order closed: %s", order_id)
        return _listed(order, await self._item_for(str(order.get("itemId") or "")))

    async def delete_order(self, order_id: str, *, platform: str = "pc") -> None:
        resp = await self._call("DELETE", f"/order/{order_id}", platform=platform)
        if resp.get("error"):
            raise SellerError(f"Failed to delete order {order_id}: {resp['error']}")
        log.info("Order deleted: %s", order_id)

    async def get_my_orders(self) -> list[dict]:
        """[{'item': name, 'slug': slug, 'order_data': raw order}] for every order you have."""
        data = await self._call("GET", "/orders/my")
        names = await self.market.item_names_by_id()
        out = []
        for order in data.get("data") or []:
            if not order.get("id"):
                continue
            item = names.get(str(order.get("itemId") or ""))
            out.append({"item": item.name if item else "Unknown item",
                        "slug": item.slug if item else "", "order_data": order})
        out.sort(key=lambda o: (o["order_data"].get("type") != "sell", o["item"]))
        return out

    # -------------------------------------------------------------- status
    async def set_status(self, status: str, *, minutes: int = 60) -> None:
        """Show as in game / online / invisible on warframe.market.

        Like the website, this keeps a websocket open in the background for
        ``minutes`` (warframe.market drops you back when it closes)."""
        if status not in STATUSES:
            raise SellerError(f"status must be one of {', '.join(STATUSES)}")
        if self._jwt is None:
            self._jwt = await self.login()
        if self._status_task and not self._status_task.done():
            self._status_task.cancel()
        ready: asyncio.Future = asyncio.get_running_loop().create_future()
        self._status_task = asyncio.create_task(self._hold_status(status, minutes * 60, ready))
        await asyncio.wait_for(ready, timeout=15)

    async def _hold_status(self, status: str, seconds: int, ready: asyncio.Future) -> None:
        headers = {"User-Agent": USER_AGENT, "Origin": SITE}
        try:
            async with self._sess().ws_connect(WS_URL, headers=headers, protocols=("wfm",),
                                               heartbeat=30) as ws:
                await ws.send_json({"route": "@wfm|cmd/auth/signIn", "payload": {"token": self._jwt}})
                await ws.send_json({"route": "@wfm|cmd/status/set", "payload": {
                    "status": status, "duration": seconds,
                    "activity": {"type": "UNKNOWN", "details": "",
                                 "startedAt": int(datetime.now(timezone.utc).timestamp())}}})
                if not ready.done():
                    ready.set_result(None)
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    try:
                        msg = await ws.receive(timeout=max(1.0, deadline - time.monotonic()))
                    except asyncio.TimeoutError:
                        break
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR,
                                    aiohttp.WSMsgType.CLOSE):
                        break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Status websocket failed: %s", exc)
            if not ready.done():
                ready.set_exception(SellerError(f"couldn't connect to warframe.market: {exc}"))
        finally:
            log.info("Status websocket closed")
