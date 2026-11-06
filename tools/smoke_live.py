"""Check every upstream call against the LIVE warframe.market and WARFRAME Wiki.

    python -m tools.smoke_live                 # default item: Serration
    python -m tools.smoke_live "Saryn Prime Set"

Each line prints OK / FAIL with a short summary. Run it after an API change on
either site: if a line fails, the matching parser in market/ or wiki/ needs a
look. Read-only; no account needed.
"""
from __future__ import annotations

import asyncio
import sys
import time

import aiohttp

from market.client import USER_AGENT, WarframeMarketClient
from market.insights import ducat_deals, history_summary, recent_trends
from wiki.client import WarframeWikiClient
from wiki.kinds import all_categories


async def check(name: str, coro, describe) -> bool:
    start = time.monotonic()
    try:
        result = await coro
        print(f"OK    {name:<28} {describe(result)}  ({time.monotonic() - start:.1f}s)")
        return True
    except Exception as exc:
        print(f"FAIL  {name:<28} {type(exc).__name__}: {exc}")
        return False


async def main(query: str) -> int:
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT},
                                     timeout=aiohttp.ClientTimeout(total=20)) as session:
        m = WarframeMarketClient(session)
        w = WarframeWikiClient(session)
        item = await m.resolve_item(query)
        print(f"Item: {item.name} ({item.slug}, id {item.id or '?'})\n")
        results = [
            await check("items", m.items(), lambda r: f"{len(r)} items, {sum(1 for i in r if i.ducats)} with ducats"),
            await check("item detail", m.get_item(item.slug),
                        lambda d: f"tax {d.trading_tax}, MR {d.mastery_rank}, wiki {bool(d.wiki_url)}"),
            await check("set", m.get_set(item.slug), lambda r: f"{len(r)} items in set"),
            await check("orders", m.get_orders(item.slug), lambda r: f"{len(r)} orders"),
            await check("top orders", m.get_top_orders(item.slug),
                        lambda r: f"{len(r['sell'])} sell / {len(r['buy'])} buy"),
            await check("statistics", m.get_statistics(item.slug),
                        lambda r: f"90d avg {history_summary(r['90days']).avg_price}p"),
            await check("recent orders", m.get_recent_orders(), lambda r: f"{len(r)} orders"),
        ]
        names = await m.item_names_by_id()
        recent = await m.get_recent_orders()
        print(f"      trends / ducat deals       {len(recent_trends(recent, names))} trends, "
              f"{len(ducat_deals(recent, names))} deals")
        seller = next((o.seller for o in recent if o.seller != "unknown"), None)
        if seller:
            results.append(await check("user profile", m.get_user(seller), lambda u: f"{u.name}, rep {u.reputation}"))
            results.append(await check("user orders", m.get_user_orders(seller), lambda r: f"{len(r)} orders"))
        results += [
            await check("riven weapons", m.riven_weapon_catalog(),
                        lambda r: f"{len(r)} weapons, {sum(1 for x in r if x.disposition)} with disposition"),
            await check("riven attributes", m.get_riven_attributes(), lambda r: f"{len(r)} stats"),
            await check("riven auctions", m.get_riven_auctions("acceltra"), lambda r: f"{len(r)} acceltra rivens"),
            await check("lich weapons", m.weapon_catalog("lich"), lambda r: f"{len(r)} weapons"),
            await check("sister weapons", m.weapon_catalog("sister"), lambda r: f"{len(r)} weapons"),
            await check("lich auctions", m.get_weapon_auctions("lich", "kuva_bramma"), lambda r: f"{len(r)} contracts"),
            await check("wiki search", w.search("serration"), lambda r: f"{len(r)} results"),
            await check("wiki page", w.fetch("Serration"),
                        lambda p: f"{len(p.sections)} infobox groups, {len(p.article_sections)} sections"),
            await check("wiki random", w.random_title(), lambda t: t or "nothing"),
        ]
        for cat in all_categories():
            results.append(await check(f"category {cat}", w.category_members(cat), lambda r: f"{len(r)} pages"))
        failed = results.count(False)
        print(f"\n{len(results) - failed}/{len(results)} checks passed")
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(" ".join(sys.argv[1:]) or "Serration")))
