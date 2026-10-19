"""warframe.market *write* access: sign in, post and remove your own listings.

Kept separate from market/client.py (read-only lookups) on purpose. This class
owns its own aiohttp session + cookie jar so the CSRF cookie/token and your
JWT never mix with the shared wiki/market session.

Auth, in order of preference (set in .env):
    WFM_JWT                      token copied from your browser's "JWT" cookie
    WFM_EMAIL + WFM_PASSWORD     password sign-in (may be blocked by Cloudflare)

CSRF handling:
    * a token is looked for in the site's HTML (<meta name="csrf-token">, or a
      csrf_token value in an inline script) and in any csrf* cookie
    * it is sent as X-CSRFToken together with Origin/Referer headers
    * if the server answers with a CSRF error, the token is re-fetched and the
      request is retried once
"""
from __future__ import annotations

import asyncio
import json as jsonlib
import logging
import os
from dotenv import load_dotenv
import re
from datetime import datetime, timezone
import time
from dataclasses import dataclass
import aiohttp
from yarl import URL
from .client import WarframeMarketClient
log = logging.getLogger("warframebot.market.seller")
ws_log = logging.getLogger("warframebot.market.seller.ws")
ORDER_API = "https://api.warframe.market/v2"
API = "https://api.warframe.market/v1"
SITE = "https://warframe.market"
USER_AGENT = "WarframeBOT/1.0 (personal listing tool)"
WS_URL = "wss://ws.warframe.market/socket"
MIN_GAP = 0.4  # warframe.market allows roughly 3 requests/second

_CSRF_PATTERNS = [
    re.compile(r'<meta[^>]+name=["\']csrf[-_]?token["\'][^>]*content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*name=["\']csrf[-_]?token["\']', re.I),
    re.compile(r'["\']csrf[-_]?token["\']\s*[:=]\s*["\']([^"\']+)["\']', re.I),
]


class SellerError(Exception):
    """Anything that goes wrong while signing in or posting."""
@dataclass
class RecentOrder:
    id: str
    order_type: str
    platinum: int
    quantity: int
    item: str 
    user: dict | None
    perTrade: int | None
    subtype: str | None
    rank: int | None
    charges: int | None
    timestamp: int | None
    amberStar: int | None
    cyanStar: int | None
    visible: bool
@dataclass
class SoldOrder:
    id: str
    order_type: str
    originID: str
    platinum: int
    quantity: int
    item: dict
    user: dict
@dataclass
class ListedOrder:
    id: str
    item_name: str
    order_type: str
    platinum: int
    quantity: int
    rank: int | None
    perTrade: int | None
    mod_charges: int | None
    subtype: string | None
    amberStars: int | None
    cyanStars: int | None
    visible: bool


class MarketSeller:
    def __init__(self, email: str | None = None, password: str | None = None, *,
                 jwt: str | None = None, on_token=None) -> None:
        """No arguments: the shared account from .env (WFM_JWT or WFM_EMAIL + WFM_PASSWORD).
        With email/password and/or a saved jwt: that user's own account; .env is never consulted.
        The saved jwt is used first; email and password are only used to sign in again when it
        is rejected. `on_token` is an async callable that receives every freshly issued jwt."""
        self._own = bool(email or password or jwt)
        self._email = email if self._own else os.getenv("WFM_EMAIL") 
        self._password = password if self._own else os.getenv("WFM_PASSWORD")
        self._on_token = on_token
        self._session: aiohttp.ClientSession | None = None
        self._jwt: str | None = jwt if self._own else (os.getenv("WFM_JWT") or None)
        # a token with no email/password behind it can't be renewed
        self._static_token = self._jwt is not None and not (self._email and self._password)
        self._csrf: str | None = None
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._OrderData = []

    # ------------------------------------------------------------ plumbing
    @property
    def configured(self) -> bool:
        return bool(self._jwt or (self._email and self._password))

    def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            jar = aiohttp.CookieJar()
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), cookie_jar=jar)
        return self._session

    @property
    def jwt(self) -> str | None:
        return self._jwt

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _headers(self, platform: str = "pc", *, signin: bool = False, cookies: dict = {} ) -> dict[str, str]:
        h = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Language": "en",
            "Platform": platform,
            "Origin": SITE,
            "Referer": SITE + "/",
        }
        if signin:
            h["Authorization"] = "JWT"  # sign-in is sent with a bare "JWT" header
        elif self._jwt:
            h["Authorization"] = f"JWT {self._jwt}"
        if self._csrf:
            h["X-CSRFToken"] = self._csrf
        if self._jwt and cookies == {}:
          h["Set-Cookie"] = "JWT="+self._jwt
        return h

    def _cookie(self, needle: str) -> str | None:
        jar = self._sess().cookie_jar
        for url in (URL(SITE), URL(API)):
            for name, morsel in jar.filter_cookies(url).items():
                if needle in name.lower():
                    return morsel.value # Return the value of the cookie if found
        return None

    async def _refresh_csrf(self) -> None:
        html = ""
        try:
            async with self._sess().get(
                SITE + "/", headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
            ) as r:
                html = await r.text()
        except aiohttp.ClientError as exc:
            log.warning("Could not load %s to look for a CSRF token: %s", SITE, exc)

        token = None
        for pattern in _CSRF_PATTERNS:
            m = pattern.search(html)
            if m:
                token = m.group(1)
                break
        if token is None:
            token = self._cookie("csrf") # Get CSRF token from cookies if not found in HTML
        self._csrf = token
        log.info("CSRF token %s", "found" if token else "NOT found")  # never log the actual token value

    async def _send(self, method: str, path: str, *, body=None, platform="pc", signin=False):
        async with self._lock:
            gap = MIN_GAP - (time.monotonic() - self._last)
            if gap > 0:
                await asyncio.sleep(gap)
            try:
                async with self._sess().request(
                    method, API + path, json=body, headers=self._headers(platform, signin=signin)
                ) as r:
                    text = await r.text()
                    status, headers = r.status, r.headers
            except aiohttp.ClientError as exc:
                raise SellerError(f"network error: {exc}") from exc
            finally:
                self._last = time.monotonic()
        try:
            data = jsonlib.loads(text) if text else {}
        except ValueError:
            data = {"raw": text[:200]}  # e.g. a Cloudflare HTML challenge page
        return status, headers, data
    async def _send_Data(self, method: str, path: str, *, body=None, platform="pc", signin=False, cookies = None):
        async with self._lock:
            gap = MIN_GAP - (time.monotonic() - self._last)
            if gap > 0:
                await asyncio.sleep(gap)
            try:
                if path == "/orders/recent":
                    async with self._sess().request(
                        method, ORDER_API + path, headers=self._headers(platform, signin=signin)
                    ) as r:
                        text = await r.text()
                        status, headers = r.status, r.headers
                else:

                    async with self._sess().request(
                        method, ORDER_API + path, json=body, headers=self._headers(platform, signin=signin), cookies={"JWT": self._jwt}
                    ) as r:
                        text = await r.text()
                        status, headers = r.status, r.headers
            except aiohttp.ClientError as exc:
                raise SellerError(f"network error: {exc}") from exc
            finally:
                self._last = time.monotonic()
        try:
            data = jsonlib.loads(text) if text else {}
        except ValueError:
            data = {"raw": text[:200]}  # e.g. a Cloudflare HTML challenge page
        return status, headers, data


    @staticmethod
    def _short(data) -> str:
        return str(data)[:300]
    async def ws_connect(self, jwt: str = None):
        if jwt:
            async with self._sess().ws_connect(WS_URL, headers=
            {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Sec-WebSocket-Extensions": "permessage-deflate",
            "Sec-WebSocket-Protocol": "wfm",
            "Origin": SITE,
        }) as ws:
                # Authenticate the WebSocket connection using the provided JWT token
                auth = {
                  "route": "@wfm|cmd/auth/signIn",
                  "payload": {
                    "token": jwt
                  }
                }
                # Prepare the command to set the user's status to "ingame" with activity details
                command = {
                  "route": "@wfm|cmd/status/set",
                  "payload": {
                  "status": "ingame",
                  "duration":int(1200),
                  "activity": {
                      "type": "UNKNOWN",
                      "details": "Void Fissure",
                      "startedAt": int(datetime.now().timestamp()), # Timestamp when the activity started
                    },
                  },
                }
                await ws.send_json(auth)
                await ws.send_json(command)
                try:
                  async for msg in ws:
                      if msg.type == aiohttp.WSMsgType.TEXT:
                          ws_log.info(f"Received WS data: {msg.data}")
                      elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                          ws_log.info(f"Data: {msg.data}")
                          break
                finally:
                      ws_log.info("Websocket Closed")
    async def login(self) -> None:
        email, password = self._email, self._password
        if not (email and password):
            raise SellerError("Set WFM_JWT, or WFM_EMAIL and WFM_PASSWORD, in .env")
        if self._csrf is None:
            await self._refresh_csrf()

        body = {"auth_type": "header", "email": email, "password": password, "device_id": "warframebot"}
        for attempt in range(2):
            status, headers, data = await self._send("POST", "/auth/signin", body=body, signin=True)
            if status < 400:
                break
            if attempt == 0 and "csrf" in str(data).lower():
                await self._refresh_csrf()
                continue
            raise SellerError(f"sign-in failed ({status}): {self._short(data)}")

        token = headers.get("Authorization", "")
        if token.upper().startswith("JWT "):
            token = token[4:]
        if not token:
            token = self._cookie("JWT") or ""
        if not token:
            raise SellerError("sign-in succeeded but no JWT was returned")
        log.info("Signed in to warframe.market")
        self._jwt = token
        if self._on_token is not None:
            try:
                await self._on_token(token)
            except Exception:
                log.exception("Could not save the new warframe.market token")
        bg_task = asyncio.create_task(self.ws_connect(token))
        
        return token

    async def _call(self, method: str, path: str, *, body=None, platform="pc"):
        jwt = None
        if path == "/orders/recent":
            log.info("Fetching recent orders from the market.")
            status, headers, data = await self._send_Data("GET", path, body=body, platform=platform)
            if status < 400:
                log.info(f"Successfully fetched recent orders: {self._short(data)}")
                return data
            else:
                log.error(f"Failed to fetch recent orders ({status}): {self._short(data)}")
                return data
            
            raise SellerError(f"Failed to fetch recent orders ({status}): {self._short(data)}")
        if self._csrf is None:
            await self._refresh_csrf() # Refresh CSRF token if it is not set
        if self._jwt is None:
            self._jwt = await self.login()
            if self._jwt == None:
                raise SellerError(f"ERROR JWT DID NOT RESET") # Raise an error if the JWT did not reset after login attempt
                return
        csrf_retried = relogged = False
        while True:
            status, _, data = await self._send_Data(method, path, body=body, platform=platform) # Send the HTTP request and get the response status, headers, and data
            if status < 400:
                # Return the data if the request was successful
                return data
            if "csrf" in str(data).lower() and not csrf_retried:
                csrf_retried = True
                await self._refresh_csrf()
                continue
            if status == 401 and not self._static_token and not relogged:
                relogged = True
                self._jwt = None
                self._jwt = await self.login()
                continue
            raise SellerError(f"{status}: {self._short(data)}")
    # Fetch the item name based on the item ID
    async def _item_name(self, itemId):
        data = await self._call("GET", f"/itemId/{itemId}")
        try:
          if data.get("data", {}):
              il8n = data.get("data").get("i18n")
              item_name = il8n.get("en").get("name")
              return item_name
        except ValueError:
          logging.error("Item Name could not be found")
          return "Invalid Item Name"
    # Fetch the item details based on the slug and return the item ID, max rank, and display name
    
    # Fetch the item details based on the slug
    async def _item(self, slug: str) -> tuple[str, int | None, str]:
        """Return (item_id, max_rank or None, display name) for a url_name slug."""
        data = await self._call("GET", f"/items/{slug}")
    
        entry = (data.get("data"))
        if not entry.get("id"):
            raise SellerError(f"couldn't read an item id for '{slug}'")
        name = (entry.get("i18n", {}).get("en") or {}).get("name") or slug
        return entry["id"], entry.get("maxRank", None), name
    # Looks at recent orders overall and compares it to the current market trends
    async def analyze_recent_orders(self):
        data = await self._call("GET", "/orders/recent")
        log.info("Fetching recent orders from the market. Analyzing trends...")
        recent_orders = data.get("data", [])
        trends = {}
        analyzed_trends = {}
        for i in range(min(500, len(recent_orders))):

            order = recent_orders[i]
            item_id = order.get("itemId", None)
            item_name = item_id
            if item_name not in trends:
                trends[item_name] = {"total_quantity": 0, "total_platinum": 0, "count": 0}
            trends[item_name]["total_quantity"] += order.get("quantity", 0)
            trends[item_name]["total_platinum"] += order.get("platinum", 0)
            trends[item_name]["count"] += 1
            log.info("Processing order for item: %s with quantity: %d and platinum: %d", item_name, order.get("quantity", 0), order.get("platinum", 0))
        for item_name, stats in trends.items():
            log.info("Analyzing trends for item: %s", item_name)
            
            stats["average_platinum"] = int(stats["total_platinum"] / stats["count"] if stats["count"] > 2 else 0)
            stats["total_quantity"] = stats["total_quantity"]
            
            stats["total_platinum"] = stats["total_platinum"]
            if stats["count"] > 2:
                item_real = await self._item_name(item_name)
                if item_real not in analyzed_trends:
                     analyzed_trends[item_real] = {"count": 0, "average_platinum": 0, "total_quantity": 0}
                analyzed_trends[item_real]['count'] = stats['count']
                analyzed_trends[item_real]['average_platinum'] = stats['average_platinum']
                analyzed_trends[item_real]['total_quantity'] = stats['total_quantity']
        sorted_trends = dict(sorted(analyzed_trends.items(), key=lambda x: x[1]["count"], reverse=True))
        log.info("Completed analyzing trends for all items.")
        return sorted_trends
    # Compare item prices in ducats and platinum
    async def compare_ducats(self, item: str):
        # Implementation for comparing item prices in ducats and platinum goes here
        slug = item.replace(" ", "_").lower()
        item_id, max_rank, name = await self._item(slug)
        data = await self._call("GET", f"/items/{item_id}")
        ducat_price = data.get("data", {}).get("ducats", None)
        if not ducat_price:
            ducat_price = 0
        self.client = WarframeMarketClient(self._sess(), platform="pc", crossplay=True)
        orders = await self.client.get_orders(slug=slug)
        if not orders:
            return {"item_id": item_id, "name": name, "ducats": ducat_price, "average_platinum": 0}
        platinum_prices = [order.platinum for order in orders if order.status == "ingame" or order.status == "online"]
        average_platinum = sum(platinum_prices) / len(platinum_prices) if platinum_prices else 0
        return {"item_id": item_id, "name": name, "ducats": ducat_price, "average_platinum": average_platinum}

    # Create a new order on the Warframe Market
    async def create_order(
        self,
        slug: str,
        *,
        order_type: str,
        platinum: int,
        quantity: int = 1,
        rank: int | None,
        perTrade: int | None,
        mod_charges: int | None,
        subtype: string | None,
        amberStars: int | None,
        cyanStars: int | None,
        visible: bool = True,
        platform: str = "pc",
    ) -> ListedOrder:
        item_id, max_rank, name = await self._item(slug)
        if max_rank is not None:
            if rank is None:
                raise SellerError(f"{name} is rankable - pass a rank between 0 and {max_rank}.")
            if rank > max_rank:
                raise SellerError(f"{name} only goes up to rank {max_rank}.")
        else:
            rank = None
            
        body = {
            "itemId": item_id,
            "type": order_type,
            "platinum": platinum,
            "perTrade": perTrade,
            "quantity": quantity,
            "visible": visible,
        }
        if perTrade is not None:
            body["perTrade"] = perTrade
        if rank is not None:
            body["rank"] = rank
        if mod_charges is not None:
            body["charges"] = mod_charges
        if subtype is not None:
            body["subtype"] = subtype
        if amberStars is not None:
            body["amberStars"] = amberStars
        if cyanStars is not None:
            body["cyanStars"] = cyanStars
        data = await self._call("POST", "/order", body=body, platform=platform)
        order = data.get("data")
        log.info(f"ORDER CREATED {order}")
        return ListedOrder(
            id=order.get("id", "Order not found"),
            item_name=await self._item_name(item_id),
            order_type=order_type,
            platinum=platinum,
            quantity=quantity,
            rank=rank,
            perTrade=perTrade,
            mod_charges=mod_charges,
            subtype=subtype,
            amberStars=amberStars,
            cyanStars=cyanStars,
            visible=visible,
        )
    async def sold_order(self, order_id: str, quantity: int = None):
        data = await self._call("POST", f"/order/{order_id}/close", body={"quantity": quantity} if quantity is not None else {}, platform="pc")
        order = data.get("data")
        log.info(f"ORDER MARKED AS SOLD {order}")
        return SoldOrder(
            id=order.get("id", "Order not found"),
            order_type=order.get("type"),
            originID=order.get("originId"),
            platinum=order.get("platinum"),
            quantity=order.get("quantity"),
            item=order.get("item"),
            user=order.get("user"),
        )
    async def update_order(self, order_id: str, *, order_type: str = None, quantity: int = None, platinum: int = None, platform: str = "pc", perTrade: bool = None, visible: bool = None, charges: int = None, rank: int = None, ambers: int = None, cyans: int = None) -> ListedOrder:
        body = {}
        if order_type is not None:
            body["type"] = order_type
        if quantity is not None:
            body["quantity"] = quantity
        if platinum is not None:
            body["platinum"] = platinum
        if perTrade is not None:
            body["perTrade"] = perTrade
        if visible is not None:
            body["visible"] = visible
        if charges is not None:
            body["charges"] = charges
        if rank is not None:
            body["rank"] = rank
        if ambers is not None:
            body["amberStars"] = ambers
        if cyans is not None:
            body["cyanStars"] = cyans
        data = await self._call("PATCH", f"/order/{order_id}", body=body, platform=platform)
        order = data.get("data")
        log.info(f"ORDER UPDATED {order}")
        return ListedOrder(
            id=order.get("id", "Order not found"),
            item_name=await self._item_name(order.get("itemId")),
            order_type=order.get("type"),
            platinum=order.get("platinum"),
            quantity=order.get("quantity"),
            rank=order.get("rank"),
            perTrade=order.get("perTrade"),
            mod_charges=order.get("charges"),
            subtype=order.get("subtype"),
            amberStars=order.get("amberStars"),
            cyanStars=order.get("cyanStars"),
            visible=order.get("visible"),
        )
    # Retrieve all orders created by the authenticated user
    async def get_my_orders(self):
        self._OrderData = []
        data = await self._call("GET", "/orders/my", platform="pc")
        for order in data.get("data", []):
            if order.get("id"):
                name = await self._item_name(order.get("itemId"))
                self._OrderData.append({"item": name, "order_data": order})
        return self._OrderData
                
    # Delete an order created by the authenticated user based on the order ID
    async def delete_order(self, order_id: str, *, platform: str = "pc") -> None:
        resp = await self._call("DELETE", f"/order/{order_id}", platform=platform)
        if resp.get("error"):
            raise SellerError(f"Failed to delete order {order_id}: {resp['error']}")
        log.info(f"ORDER DELETED {order_id}") # Log the successful deletion of the order
