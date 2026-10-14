# Integration notes

Drop these folders into the repo root (they sit next to your existing `wiki/` and `cogs/`).
This package deliberately does NOT include wiki/__init__.py: your existing one defines WikiError / PageNotFoundError.

New files
    wiki/ability.py            fetch + parse ability pages (pure, tested)
    wiki/ability_view.py       embed + dropdown view (image shown below the data)
    (cogs/wiki_commands.py     see the separate drop-in file: adds /wf ability + routes /wf page)
    market/client.py           warframe.market v2 client (rate limit, cache)
    market/analysis.py         seller maths: price tiers, competition, whispers (pure, tested)
    market/embeds.py           Discord embeds for the seller dashboard
    cogs/market_commands.py    /wm price | buyers | sellers
    api/main.py                FastAPI app (same logic as the bot)
    http_session.py            shared aiohttp session helper (put at repo root)
    tools/inspect_ability.py   check the parser against the live wiki
    tests/                     35 tests (run: python -m pytest)

1. pip install -r requirements-additions.txt
2. In bot.py, next to your existing load_extension call:
       bot.load_extension("cogs.market_commands")
3. Replace cogs/wiki_commands.py with the updated drop-in (adds /wf ability and makes
   /wf page use the rich view for ability pages).
4. API:  uvicorn api.main:app --reload     then open http://127.0.0.1:8000/docs
   Set WFBOT_API_KEY to require an X-API-Key header; WFBOT_CORS to restrict origins.
5. Add the .gitignore lines and `git rm --cached error.log pyvenv.cfg _tmp_*`.
