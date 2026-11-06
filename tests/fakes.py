"""Shared offline fakes: an aiohttp-like session that answers from a route table."""
from __future__ import annotations

import asyncio


class FakeResp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    async def json(self, content_type=None):
        return self._body

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class RouteSession:
    """``routes`` maps a URL suffix to a body, a (status, body) tuple, or a list
    of those (consumed in order, last one repeats). Longest suffix wins."""

    def __init__(self, routes):
        self.routes = routes
        self.calls: list[tuple[str, dict | None, dict | None]] = []

    def get(self, url, params=None, headers=None, **kwargs):
        self.calls.append((url, params, headers))
        for suffix in sorted(self.routes, key=len, reverse=True):
            if url.endswith(suffix):
                answer = self.routes[suffix]
                if isinstance(answer, list):
                    answer = answer.pop(0) if len(answer) > 1 else answer[0]
                status, body = answer if isinstance(answer, tuple) else (200, answer)
                return FakeResp(status, body)
        return FakeResp(404, {})

    def urls(self):
        return [c[0] for c in self.calls]


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def v2(data):
    return {"apiVersion": "0.0.0", "data": data, "error": None}


ITEMS = v2([
    {"id": "set1", "slug": "saryn_prime_set", "tags": ["prime", "set", "warframe"], "ducats": None,
     "vaulted": True, "i18n": {"en": {"name": "Saryn Prime Set", "icon": "items/saryn.png",
                                      "thumb": "items/saryn.thumb.png"}}},
    {"id": "p1", "slug": "saryn_prime_blueprint", "tags": ["prime", "blueprint"], "ducats": 45,
     "i18n": {"en": {"name": "Saryn Prime Blueprint"}}},
    {"id": "p2", "slug": "saryn_prime_systems_blueprint", "tags": ["prime", "component"], "ducats": 100,
     "i18n": {"en": {"name": "Saryn Prime Systems Blueprint"}}},
    {"id": "m1", "slug": "serration", "tags": ["mod"], "maxRank": 10,
     "i18n": {"en": {"name": "Serration"}}},
    {"id": "a1", "slug": "ayatan_anasa_sculpture", "maxAmberStars": 2, "maxCyanStars": 2,
     "i18n": {"en": {"name": "Ayatan Anasa Sculpture"}}},
])


def user(name="Tenno", status="ingame", rep=10):
    return {"ingameName": name, "status": status, "reputation": rep, "platform": "pc"}


def order(oid, side, plat, item_id="p1", status="ingame", name=None, rank=None, qty=1):
    o = {"id": oid, "type": side, "platinum": plat, "quantity": qty, "visible": True, "itemId": item_id,
         "user": user(name or f"T{oid}", status)}
    if rank is not None:
        o["rank"] = rank
    return o
