"""Check the ability parser against the LIVE wiki:

    python -m tools.inspect_ability Nurinarim
    python -m tools.inspect_ability "Molecular Prime"

Prints what was parsed (description, stats, each section, image URL). If a
section is missing or something looks off, run with --html to dump the raw
parsed HTML to ability_debug.html and compare the markup with wiki/ability.py.
"""
import asyncio
import sys

import aiohttp

from wiki.ability import API_URL, USER_AGENT, fetch_ability


async def main(title: str, dump_html: bool) -> None:
    async with aiohttp.ClientSession() as s:
        page = await fetch_ability(s, title)
        if page is None:
            print("Page not found")
            return
        print(f"title:        {page.title}  (is_ability={page.is_ability})")
        print(f"image:        {page.image_url}")
        print(f"introduced:   {page.introduced}   energy: {page.energy_cost}")
        print(f"description:  {page.description}")
        for k, v in page.stats.items():
            print(f"stat {k}: {v}")
        for name, lines in page.sections.items():
            print(f"\n== {name} ({len(lines)} lines) ==")
            print("\n".join(lines[:6]) + ("\n..." if len(lines) > 6 else ""))
        if dump_html:
            params = {"action": "parse", "page": title, "prop": "text", "format": "json",
                      "formatversion": 2, "redirects": 1}
            async with s.get(API_URL, params=params, headers={"User-Agent": USER_AGENT}) as r:
                open("ability_debug.html", "w", encoding="utf-8").write((await r.json())["parse"]["text"])
            print("\nwrote ability_debug.html")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    asyncio.run(main(args[0] if args else "Nurinarim", "--html" in sys.argv))
