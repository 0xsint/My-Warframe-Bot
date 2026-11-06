# WarframeBOT

A Discord **user-installable app** for WARFRAME players, with two command groups:

- **`/wf`**: the official [WARFRAME Wiki](https://wiki.warframe.com). Warframes, weapons, mods, arcanes, companions, enemies, relics, quests and abilities, plus drop tables, side-by-side comparisons, search and random pages.
- **`/wm`**: [warframe.market](https://warframe.market). Pricing advice, buyers and sellers with copy-paste whispers, item facts, set-vs-parts value, 90-day history, ducat deals, market trends, player profiles, and riven / Kuva Lich / Sister of Parvos contracts. The owner can also post and manage their own listings and online status.

The same market logic is also served by a **REST API** (FastAPI), so the API and the bot always show the same numbers.

Built with [nextcord](https://docs.nextcord.dev/), aiohttp, BeautifulSoup and FastAPI.

> **Unofficial project.** Not affiliated with or endorsed by Digital Extremes or warframe.market. WARFRAME is a trademark of Digital Extremes Ltd.

---

## Quick start

```bash
git clone https://github.com/0xsint/My-Warframe-Bot.git
cd My-Warframe-Bot
python -m venv .venv
.venv/bin/pip install -r requirements.txt    # Windows: .\.venv\Scripts\pip install -r requirements.txt
cp .env.example .env                          # then put your bot token in .env
.venv/bin/python bot.py                       # Windows: .\.venv\Scripts\python bot.py
```

Before the commands appear in Discord, configure the **Installation** tab in the Developer Portal (see [Setup](#setup)).

Optional REST API:

```bash
uvicorn api.main:app --env-file .env          # docs at http://127.0.0.1:8000/docs
```

---

## Commands

Everything works in servers, DMs and group DMs. Every name option has live autocomplete. Results use one shared layout: an overview embed, a **dropdown** to jump between sections, and **Prev/Next** buttons for long sections. The controls stop working after 10 minutes, but link buttons keep working.

### `/wf`: WARFRAME Wiki

| Command | What you get |
| --- | --- |
| `/wf warframe name` | Stats, passive and abilities |
| `/wf weapon name` | General info, utility, and every attack mode |
| `/wf mod name` | Effect per rank, polarity, drain, drop locations |
| `/wf arcane name` | Effect and where it drops |
| `/wf companion name` | Sentinels, kubrows, kavats, MOAs, hounds |
| `/wf enemy name` | Health, armor, weaknesses, drops |
| `/wf relic name` | Rewards and where to farm it |
| `/wf quest name` | Requirements, rewards, walkthrough |
| `/wf ability name` | Rich ability view: stats, energy, Info, Kit Interactions, Synergies, Tips, Notes, Trivia, Bugs |
| `/wf page name` | Any wiki page (ability pages get the rich ability view) |
| `/wf drops name` | Opens the page on its *Acquisition* / drop table section |
| `/wf compare first second` | Two pages' infobox stats side by side, with the rows that differ marked |
| `/wf search query` | Up to 50 results, then open any of them from the dropdown |
| `/wf random` | A random wiki article |

- The typed commands (`warframe` … `quest`, `ability`) only suggest pages of that kind. Suggestions come from the matching wiki categories, which load in the background at startup. Until they have loaded, normal title search is used.
- Wiki tables (drop tables, relic rewards, …) are shown as one line per row.
- Pages about tradable items get a **warframe.market** link button.

### `/wm`: warframe.market

Most commands take an optional `platform` (PC, PlayStation, Xbox, Switch, Mobile; default PC). Prices only count players who are online or in game, because only they can trade.

| Command | What you get |
| --- | --- |
| `/wm price item [rank] [my_price]` | Seller dashboard: fast / balanced / patient list price, best instant sale, competition ladder, ranks, 48h sales, tips, and where *your* price would rank |
| `/wm buyers item [rank]` | Online buyers, highest first, with copy-paste whispers |
| `/wm sellers item [rank]` | Cheapest online sellers, with whispers |
| `/wm top item [rank] [charges] [subtype] [amber_stars] [cyan_stars]` | Best 5 online buyers and sellers, filtered server-side (requiem charges, relic refinement, Ayatan stars, …) |
| `/wm item item` | Item facts: rarity, mastery rank, max rank, trading tax, ducats, endo, vosfor, vault status, subtypes, wiki link, and current best prices |
| `/wm set item` | Set price vs the sum of its parts (part quantities included), total ducats, and whether buying parts to sell the set makes money |
| `/wm history item [rank]` | 90-day average / median / low / high, week-over-week trend, a sparkline, and the last 48 hours |
| `/wm ducats item` | Trade it to Baro for ducats, or sell it for platinum? |
| `/wm ducat_deals [min_ducats]` | The cheapest prime parts per ducat on sale right now, for buying before Baro arrives |
| `/wm trends` | Busiest items among the newest orders on the site |
| `/wm recent [side]` | The newest orders posted site-wide |
| `/wm user name` | A player's reputation, status, platform, bio, and all their listings (with whispers) |
| `/wm riven weapon [stat1..3] [negative] [polarity] [rank] [max_price]` | Riven contracts for a weapon, filtered by stats, plus the weapon's riven disposition. Family names work, e.g. `kuva lich` |
| `/wm lich weapon [element] [min_damage] [ephemera] [max_price]` | Kuva Lich weapon contracts (Kuva weapons) |
| `/wm sister weapon [element] [min_damage] [ephemera] [max_price]` | Sister of Parvos weapon contracts (Tenet weapons) |

**Owner only** (your Discord ID must be in `OWNER_ID`; these act on *your* warframe.market account):

| Command | What it does |
| --- | --- |
| `/wm list item price [quantity] [order_type] [rank] [per_trade] [charges] [subtype] [amber_stars] [cyan_stars] [visible]` | Post a listing |
| `/wm update_order order_id [...]` | Change price, quantity, visibility, rank, … |
| `/wm sold order_id [quantity]` | Mark a trade as done, so it counts in warframe.market's statistics |
| `/wm unlist order_id` | Remove a listing |
| `/wm my_orders` | All your listings, filterable by selling / buying |
| `/wm status status [minutes]` | Show as in game / online / invisible on warframe.market |

`order_id` autocompletes from your own listings (labelled like `SELL Serration · 20p ×1`), so you never need to copy IDs.

---

## Environment variables

See [`ENVIRONMENT_VARIABLES.txt`](ENVIRONMENT_VARIABLES.txt) for details and [`.env.example`](.env.example) for a template.

| Variable | Needed for |
| --- | --- |
| `DISCORD_TOKEN` | Always |
| `OWNER_ID` | Owner-only `/wm` commands |
| `WFM_JWT` *or* `WFM_EMAIL` + `WFM_PASSWORD` | Owner-only `/wm` commands |
| `WFM_PLATFORM`, `LOG_LEVEL`, `LOG_FILE` | Optional |
| `WFBOT_API_KEY`, `WFBOT_CORS` | Optional, REST API only |

---

## REST API

`uvicorn api.main:app --env-file .env`, then open `/docs` for interactive Swagger docs. When `WFBOT_API_KEY` is set, every `/v1/*` endpoint needs the header `X-API-Key: <key>`.

| Endpoint | Returns |
| --- | --- |
| `GET /health` | `{"status": "ok"}` |
| `GET /v1/items/search?q=` | Matching tradable items |
| `GET /v1/items/{slug}` | Item facts (tax, ducats, MR, endo, wiki link, …) |
| `GET /v1/items/{slug}/report` | The `/wm price` seller report (`rank`, `my_price`, `history`) |
| `GET /v1/items/{slug}/orders` | Filtered raw orders with whispers |
| `GET /v1/items/{slug}/top` | Best 5 buy/sell orders (`rank`, `charges`, `subtype`, `amber_stars`, `cyan_stars`) |
| `GET /v1/items/{slug}/whispers` | Ready-to-paste whispers |
| `GET /v1/items/{slug}/set` | Set vs parts |
| `GET /v1/items/{slug}/history` | 90-day / 48-hour summaries plus the raw daily points |
| `GET /v1/recent`, `/v1/trends`, `/v1/ducats/deals` | Site-wide activity |
| `GET /v1/users/{name}` | Profile and listings |
| `GET /v1/rivens/{weapon}` | Riven contracts (`positive` repeatable, `negative`, `polarity`, …) |
| `GET /v1/lich/{weapon}`, `/v1/sister/{weapon}` | Weapon contracts (`element`, `min_damage`, `ephemera`, …) |

`{slug}` accepts a slug or a name (`serration`, `Mirage Prime Set`). Upstream 404s become 404, rate limits become 503 with `Retry-After`, and other upstream failures become 502.

---

## How the pricing advice works

- **Fast sale**: 1p under the cheapest online seller, but never below the best online buy order (if a buyer pays more, sell to them instead).
- **Balanced**: the 25th percentile of online asks. **Patient**: the median.
- One lowball listing far under the rest (under 70% of the next one, and at least 5p less) is treated as bait or a mistake and ignored.
- Tips flag mixed mod ranks, instant-sale chances, wide spreads, crowded markets, and asks that are far from recent sales.

Ducat advice uses the common rule of thumb that 10 or more ducats per platinum favours ducats.

---

## Project layout

```
bot.py                 entry point: one shared HTTP session, cache warm-up, logging
cogs/
  wiki_commands.py     /wf
  market_commands.py   /wm
wiki/
  client.py            MediaWiki API: search, parse, categories, random (cached)
  parser.py            infobox groups, lead paragraphs, article sections, tables
  ability.py           ability page parser
  kinds.py             which categories feed each typed /wf command
  page_view.py         page -> dropdown view; drops_section()
  compare.py           /wf compare
  embeds.py, ability_view.py, search_view.py
market/
  models.py            dataclasses + defensive parsers (items, orders, users, auctions, stats)
  client.py            public API client: rate limit, retry, cache, every read endpoint
  analysis.py          seller report (price tiers, tips, whispers)
  insights.py          sets, history, ducat deals, trends, lich/sister filters
  riven.py             riven filters and stat matching
  seller.py            your account: listings, close, delete, status (owner only)
  embeds.py            every /wm embed
core/fuzzy.py          forgiving name search (exact > prefix > substring > words > typo)
ui/                    SectionView (dropdown + Prev/Next + link buttons), text helpers
api/main.py            FastAPI service
tools/smoke_live.py    checks every upstream call against the live sites
tools/inspect_ability.py   checks the ability parser against a live page
tests/                 offline tests (no network)
```

### Upstream APIs

- **warframe.market v2** (`api.warframe.market/v2`): `items`, `item/{slug}`, `item/{slug}/set`, `orders/item/{slug}`, `orders/item/{slug}/top`, `orders/recent`, `user/{slug}`, `orders/user/{slug}`, `riven/weapons`, `riven/attributes`, `lich/*`, `sister/*`; for your own account: `order` (POST/PATCH/DELETE), `order/{id}/close`, `orders/my`, plus the `ws.warframe.market` websocket for status.
- **warframe.market v1**: `items/{slug}/statistics` (history) and `auctions/search` (riven / lich / sister contracts), which have no v2 equivalent yet.
- **WARFRAME Wiki** (`wiki.warframe.com/api.php`): `opensearch`, `parse`, `query` (categorymembers, random, pageimages, categories).

All warframe.market calls share one limiter (at most about 3 requests a second, as the site asks), a short cache, and one retry on HTTP 429/5xx.

---

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

The tests run offline against fake upstreams: parsers, the client (headers, caching, retries), analysis, every embed (checked against Discord's size limits), the views, the command tree (Discord's option rules), every `/wm` and `/wf` command end to end, and the REST API.

To check the parsers against the **live** sites (for example after one of them changes its API):

```bash
python -m tools.smoke_live "Saryn Prime Set"
```

---

## Setup

1. **Create an application** in the [Discord Developer Portal](https://discord.com/developers/applications).
2. **Bot tab**: click *Reset Token* and copy it into `.env` as `DISCORD_TOKEN`. No privileged intents are needed.
3. **Installation tab** (the step most often missed):
   - Under *Installation Contexts*, tick **User Install** (and **Guild Install** if you want it in servers).
   - Set *Install Link* to **Discord Provided Link**.
   - *Default Install Settings*: User Install gets `applications.commands`. Guild Install gets `applications.commands` and `bot`.
   - Save, open the install link, and pick *Add to my apps* or *Add to server*.
4. **Run** `python bot.py`. The log shows the user-install invite URL, the registered commands, and whether the global command sync worked.

New commands can take a few minutes to show up in Discord. Restart your Discord client (Ctrl+R) if they don't.

## Troubleshooting

- **Commands don't show up**: re-check the Installation tab scopes, then wait a few minutes and press Ctrl+R. The log says if Discord refused the sync (403).
- **"Only the bot owner can manage listings"**: put your Discord user ID in `OWNER_ID`.
- **"warframe.market rejected the token (401)"**: `WFM_JWT` expired. Copy a fresh `JWT` cookie from your browser.
- **"warframe.market rate limit hit"**: wait a moment. The bot already spaces its requests and retries once.
- **"WARFRAME Wiki blocked this request" (403)**: Cloudflare is challenging the server's IP. This is common on cloud hosts; a home connection usually works.

## Security notes

- The owner commands are gated by `OWNER_ID`, because a user-installed app can be run by anyone.
- Your warframe.market token and password are never logged. The token lives in its own session, separate from public lookups.
- Owner command replies are ephemeral (only you see them).
- The REST API is read-only. Set `WFBOT_API_KEY` before exposing it publicly.

## Credits

Data from the [WARFRAME Wiki](https://wiki.warframe.com) (CC BY-SA) and [warframe.market](https://warframe.market). Please respect both sites' rate limits.
