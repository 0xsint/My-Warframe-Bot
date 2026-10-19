# WarframeBOT

A Discord **user-installable app** for Warframe players. It does two jobs:

- **`/wf`**: look up Warframes, weapons, abilities and any other page on the official [WARFRAME Wiki](https://wiki.warframe.com).
- **`/wm`**: seller-focused [warframe.market](https://warframe.market) tools: pricing advice, buyers to whisper, competition, and (for the owner) posting and managing your own listings.

The same market logic is also exposed as a **REST API** built with FastAPI, so the API and the Discord commands always show the same numbers.

Built with [nextcord](https://docs.nextcord.dev/), aiohttp, BeautifulSoup and FastAPI.

> **Unofficial project.** Not affiliated with or endorsed by Digital Extremes or warframe.market. WARFRAME is a trademark of Digital Extremes Ltd.

---

## Table of contents

1. [Features](#features)
2. [Quick start](#quick-start)
3. [Command reference](#command-reference)
4. [Environment variables](#environment-variables)
5. [REST API reference](#rest-api-reference)
6. [How the pricing advice works](#how-the-pricing-advice-works)
7. [Architecture](#architecture)
8. [External APIs used](#external-apis-used)
9. [Tests and dev tools](#tests-and-dev-tools)
10. [Full setup guide](#full-setup-guide)
11. [Troubleshooting](#troubleshooting)
12. [Security notes](#security-notes)
13. [Known issues](#known-issues-found-while-documenting)
14. [Credits and licensing](#credits-and-licensing)

---

## Features

**Wiki (`/wf`)**
- Warframe stats, passive and abilities; weapon stats and per-attack-mode damage.
- Rich **ability pages**: description, stats, energy cost, and a dropdown for Info, Kit Interactions, Synergies, Tips, Notes, Trivia and Bugs, with Prev/Next paging.
- Generic lookup for any wiki page (mods, enemies, relics, quests, factions, ...).
- Wiki search, plus live autocomplete on every name field.

**Market (`/wm`)**
- Seller dashboard: suggested listing prices (fast, balanced, patient), best instant-sale price, competition, 48-hour sales history, and plain-English tips.
- Online buyers with copy-paste in-game whispers.
- Cheapest online competing sellers.
- Ducat vs platinum comparison and recent-market trend analysis.
- Owner-only: post, update, remove and list your own warframe.market orders.

**Platform**
- Works in any server, DM or group DM (user install), and can also be added to servers (guild install).
- No privileged intents and no special permissions needed.
- Rate limiting and in-memory caching for both upstream sites.
- A FastAPI service with Swagger docs, optional API-key protection and CORS control.

---

## Quick start

```bash
git clone https://github.com/0xsint/My-Warframe-Bot.git
cd My-Warframe-Bot
python -m venv .venv
.venv/bin/pip install -r requirements.txt python-dotenv   # Windows: .\.venv\Scripts\pip ...
cp .env.example .env                                      # then edit .env
.venv/bin/python bot.py                                   # Windows: .\.venv\Scripts\python bot.py
```

`python-dotenv` is imported by the code but is not yet listed in `requirements.txt`, so install it explicitly (see [Known issues](#known-issues-found-while-documenting)).

Before `/wf` and `/wm` appear in Discord, you must also configure the **Installation** tab in the Developer Portal. See the [full setup guide](#full-setup-guide).

**Run the REST API (optional)**

```bash
uvicorn api.main:app --reload --env-file .env
# open http://127.0.0.1:8000/docs
```

---

## Command reference

Two slash command groups are registered globally, usable in servers, DMs and group DMs.

| Group | Subcommand | Who can run it | Purpose |
| --- | --- | --- | --- |
| `/wf` | `warframe` | Anyone | Stats, passive and abilities |
| `/wf` | `ability` | Anyone | Rich ability view |
| `/wf` | `weapon` | Anyone | Weapon stats |
| `/wf` | `page` | Anyone | Any wiki page |
| `/wf` | `search` | Anyone | Search wiki titles |
| `/wm` | `price` | Anyone | Seller dashboard |
| `/wm` | `buyers` | Anyone | Online buyers + whispers |
| `/wm` | `sellers` | Anyone | Cheapest online sellers |
| `/wm` | `ducats_compare` | Anyone | Ducats vs platinum |
| `/wm` | `riven` | Anyone | Riven contracts (auctions) for a weapon |
| `/wm` | `lich` / `sister` | Anyone | Kuva Lich / Sister of Parvos auctions |
| `/wm` | `link` / `unlink` | **Allow-listed** | Store / delete your warframe.market login |
| `/wm` | `trends` | Anyone | Recent market trends |
| `/wm` | `list` | **Allow-listed** | Post a listing |
| `/wm` | `update_order` | **Allow-listed** | Edit a listing |
| `/wm` | `unlist` | **Allow-listed** | Remove a listing |
| `/wm` | `my_orders` | **Allow-listed** | List your orders |

"Owner" means a Discord user ID listed in `OWNER_ID`.

### `/wf` : WARFRAME Wiki

All `name` options have live autocomplete (up to 10 wiki title suggestions). Names are resolved with fuzzy wiki search, so partial names and typos usually work.

Every wiki lookup below (`warframe`, `weapon`, `page`, and picked `search` results) uses the same view as `/wf ability`: an **Overview** embed, a **dropdown** listing every other block of the page (infobox groups such as *General Information* and *Abilities*, then article headings such as *Acquisition* and *Trivia*), and **Prev/Next** buttons for sections too long for one embed. The footer shows the section and page number, and controls are disabled after 10 minutes. Pages with nothing beyond the overview just show the overview.

#### `/wf warframe name:<Warframe>`
Overview: description and passive, a **Stats** field and an **Abilities** field. The dropdown puts *General Information* and *Abilities* first, then everything else on the page. Marked `[BETA]` in Discord.

#### `/wf ability name:<ability>`
Rich view for a Warframe ability (for example *Nurinarim* or *Molecular Prime*): description, energy cost, introduced date, Strength/Duration/Range/Misc stats, and the ability icon. A dropdown switches between **Overview** and any of **Info, Kit Interactions, Synergies, Tips, Notes, Trivia, Bugs** the page has; Prev/Next buttons page through long sections. Controls are disabled after 10 minutes. If the page isn't in the wiki's *Warframe Abilities* category you get a "Not an ability page" error.

#### `/wf weapon name:<Weapon>`
Overview: General Information and the Utility block. The dropdown lists General Information, Utility and the attack modes (Normal, Charged, Heavy, Incarnon, ...) first, then the article sections.

#### `/wf page name:<page>`
Generic lookup. Ability pages get the ability view; Warframe and weapon pages are detected from their infobox and get the matching overview; anything else gets a summary overview. Every page gets the section dropdown.

#### `/wf search query:<text>`
Up to 50 matching titles, 10 per page, as numbered links. The dropdown lists the results on the current page; picking one replaces the list with that page's view (ability pages included).

### `/wm` : warframe.market

Shared options:

| Option | Type | Notes |
| --- | --- | --- |
| `item` | String, autocomplete | Item name, e.g. `Mirage Prime Set`. Resolved with fuzzy search. |
| `rank` | Integer 0-10, optional | Mod/arcane rank. `0` = unranked. Omit to include all ranks. |
| `platform` | Choice, default PC | PC, PlayStation (`ps4`), Xbox, Switch |

All `/wm` lookups are paginated or sectioned. Controls work like `/wf`: a dropdown where there are several sections, Prev/Next where there are several pages.

#### `/wm price item [rank] [platform] [my_price]`
The seller dashboard, split into dropdown sections: **Overview** (market tone, fast / balanced / patient prices, instant-sale price, your price verdict when `my_price` is given, item image), **Competition** (stats and price ladder), **By mod rank** (when ranks are mixed), **Last 48h sales** and **Tips**. Prev/Next walks through every section in order.

#### `/wm buyers item [rank] [platform]`
Up to 48 online buyers (highest offer first), 6 per page, each page with its own copy-paste `/w` whispers.

#### `/wm sellers item [rank] [platform]`
Up to 48 cheapest online sellers (your competition), 6 per page, with whispers.

#### `/wm riven weapon [stat1] [stat2] [stat3] [negative] [polarity] [rank] [max_price] [online_only] [platform]`
Riven mod contracts (auctions) for one weapon, cheapest first, 4 per page. Read-only.

- `weapon`: autocompletes from the riven-eligible weapon list, so Kuva Lich weapons (Kuva Bramma, ...) and Sister of Parvos weapons (Coda Bassocyst, ...) are included.
- `stat1`-`stat3`: positive stats the riven must have. Each has stat autocomplete. Words are matched against the stat name by prefix, so `crit damage` finds Critical Damage and `multi` finds Multishot. A stat no listed riven has gets a "no riven matches" reply, so typos are caught.
- `negative`: only rivens whose negative stat (the curse, shown as **(negative)** in the list) is this stat, e.g. `recoil`. Also autocompletes.
- `polarity`: Madurai, Vazarin or Naramon. `rank`: 0-8 (omit for all).
- `max_price`: highest buyout (or current bid if there is no buyout) to show.
- `online_only`: default on, keeps sellers who are in game or online.

Prices are the asking price. Auctions without a buyout show the top bid. Hidden and closed auctions are not shown.

#### `/wm ducats_compare item`
Ducat value, average online platinum price, ducats per plat and a rule-of-thumb verdict (about 10+ ducats per plat favours ducating). The item name is converted to a slug directly (lower-case, spaces to underscores), so use the exact name.

#### `/wm trends`
Looks at up to 500 of the most recent orders, groups them by item, and ranks items with more than 2 recent orders by order count (medals for the top 3), 10 per page, with units and average platinum. Can take a while.

#### `/wm list item price [quantity] [order_type] [rank] [platform] [visible] [pertrade] [mod_charges] [subtype] [amberstars] [cyanstars]` (owner only)
Posts an order on your warframe.market account.

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `item` | String | required | Item name (autocomplete) |
| `price` | Integer | required | Platinum per unit, at least 1 |
| `quantity` | Integer 1-9999 | 1 | How many |
| `order_type` | Sell / Buy | Sell | Order side |
| `rank` | Integer 0-10 | none | **Required** for rankable items, and cannot exceed the item's max rank |
| `platform` | Choice | PC | Platform |
| `visible` | Boolean | true | Whether the order is visible on warframe.market |
| `pertrade` | Integer | none | Amount per trade |
| `mod_charges` | Integer | none | Charges (mods) |
| `subtype` | String | none | Item subtype |
| `amberstars` | Integer | none | Amber stars (sculptures) |
| `cyanstars` | Integer | none | Cyan stars (sculptures) |

Replies with a card showing the listing (type, price, quantity and any rank/charges/stars) and the new order ID, which you need for `update_order` and `unlist`.

#### `/wm update_order order_id order_type [quantity] [platinum] [platform] [per_item] [visible] [charges] [rank] [ambers] [cyans]` (owner only)
Edits an existing order. `order_id` and `order_type` (Sell/Buy) are required; every other option is only sent if you provide it. The reply shows the order as it now stands, with a pencil mark on the fields you changed.

#### `/wm unlist order_id [platform]` (owner only)
Removes one of your orders.

#### `/wm my_orders` (owner only)
Lists your orders 5 per page with item, side, price, quantity, rank, visibility and order ID. When you have both kinds, a dropdown filters **All / Selling / Buying**.

---

## Environment variables

Configuration comes from a `.env` file (loaded by `bot.py`). A detailed reference is in [`ENVIRONMENT_VARS.txt`](ENVIRONMENT_VARS.txt).

| Variable | Used by | Required | Description |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | Bot | **Yes** | Bot token (Developer Portal, Bot tab). The bot exits without it. |
| `OWNER_ID` | Bot | For owner commands | Your Discord user ID(s), comma-separated. If empty, every owner command is refused. |
| `WFM_JWT` | Bot | For owner commands | Your warframe.market `JWT` cookie value. Preferred over email/password. |
| `WFM_EMAIL` | Bot | Alternative to JWT | warframe.market account email |
| `WFM_PASSWORD` | Bot | Alternative to JWT | warframe.market account password |
| `WFBOT_API_KEY` | API | No | If set, `/v1/*` requires an `X-API-Key` header with this value. |
| `WFBOT_CORS` | API | No | Comma-separated allowed CORS origins. Default `*`. |

Example `.env`:

```env
DISCORD_TOKEN="your-bot-token-here"
OWNER_ID="123456789012345678"

# warframe.market account: use a JWT OR email + password
WFM_JWT=""
WFM_EMAIL=""
WFM_PASSWORD=""

# REST API (optional)
WFBOT_API_KEY=""
WFBOT_CORS="*"
```

The API does not call `load_dotenv()`, so pass `--env-file .env` to uvicorn or export the variables yourself.

---

## REST API reference

A FastAPI service that gives seller-focused views of warframe.market. It reuses `market/client.py` and `market/analysis.py`, so numbers match the Discord commands.

**Run:** `uvicorn api.main:app --reload --env-file .env`
**Interactive docs (Swagger UI):** `http://127.0.0.1:8000/docs`
**Version:** 1.0.0 &nbsp; **Methods:** `GET` only

All upstream calls share one rate limiter (about 3 requests/second) and a short cache, so heavy use of this API does not hammer warframe.market.

### Authentication

If `WFBOT_API_KEY` is set, send it on every `/v1/*` request:

```
X-API-Key: your-key
```

A missing or wrong key returns `401 Missing or invalid X-API-Key`. `/health` is always open.

### Common parameters

| Parameter | Values | Default | Notes |
| --- | --- | --- | --- |
| `platform` | `pc`, `ps4`, `xbox`, `switch` | `pc` | |
| `crossplay` | `true` / `false` | `true` | |
| `rank` | integer 0-10 | none | Mod/arcane rank filter |
| `{slug}` | item slug **or** name | | Resolved with fuzzy search, so `mirage_prime_set` and `Mirage Prime Set` both work |

### Endpoints

#### `GET /health`
Returns `{"status": "ok"}`. No auth.

#### `GET /v1/items/search`
Find tradable items by name.

| Query param | Type | Default | Notes |
| --- | --- | --- | --- |
| `q` | string, 1-80 chars | required | Search text |
| `limit` | integer 1-50 | 10 | |

```json
{
  "query": "mirage",
  "results": [
    {
      "slug": "mirage_prime_set",
      "name": "Mirage Prime Set",
      "image": "https://warframe.market/static/assets/...",
      "tags": ["prime", "set", "warframe"],
      "max_rank": null,
      "url": "https://warframe.market/items/mirage_prime_set"
    }
  ]
}
```

#### `GET /v1/items/{slug}/report`
Seller report: price tiers, competition, buyers and tips. Response is cached by clients for 30 seconds (`Cache-Control: public, max-age=30`).

| Query param | Type | Default | Notes |
| --- | --- | --- | --- |
| `rank` | integer 0-10 | none | |
| `platform` | enum | `pc` | |
| `crossplay` | boolean | `true` | |
| `history` | boolean | `true` | Include last-48h sales summary (best effort) |
| `my_price` | integer >= 1 | none | Adds a `my_price` assessment |

The response contains every field of the seller report plus `platform`, `crossplay`, `image`, `url`, and optionally `my_price`:

| Field | Meaning |
| --- | --- |
| `item_name`, `slug`, `rank_filter` | Identity and the rank filter applied |
| `online_sellers`, `total_sell_orders` | Online sellers vs all sell orders |
| `online_buyers`, `total_buy_orders` | Online buyers vs all buy orders |
| `lowest_sell`, `second_lowest_sell` | Cheapest online asks |
| `p25_sell`, `median_sell`, `p75_sell`, `mean_sell` | Online ask distribution |
| `highest_buy`, `spread` | Best online bid, and `lowest_sell - highest_buy` |
| `offline_median_sell` | Median ask of offline sellers (context only) |
| `fast_price`, `balanced_price`, `patient_price` | Suggested listing prices |
| `quick_sell_price` | Best open buy order (instant sale) |
| `buyers_at_or_above_fast` | Online buyers who would pay at least the fast price |
| `market_tone` | One-line market description |
| `outlier_low` | Cheapest listing looks like bait or a mistake |
| `mixed_ranks` | Several ranks are mixed in the numbers |
| `price_ladder` | `[{price, orders}]` for the cheapest online prices |
| `online_sell_prices` | All online ask prices, ascending |
| `rank_table` | `[{rank, lowest_sell, online_sellers, highest_buy}]` |
| `history` | `{volume_48h, avg_price_48h, median_48h}` or `null` |
| `tips` | List of plain-English suggestions |
| `my_price` | Only when `my_price` was sent: `{price, sellers_cheaper, sellers_tied, online_sellers, pct_vs_median, verdict}` |

#### `GET /v1/items/{slug}/orders`
Raw orders, filtered. Also cached for 30 seconds by clients.

| Query param | Type | Default | Notes |
| --- | --- | --- | --- |
| `side` | `sell`, `buy`, `all` | `all` | |
| `online_only` | boolean | `true` | Only online/in-game players |
| `rank` | integer 0-10 | none | |
| `limit` | integer 1-500 | 50 | |
| `platform`, `crossplay` | | | |

```json
{
  "item": "Serration",
  "slug": "serration",
  "count": 2,
  "orders": [
    {
      "id": "...", "type": "sell", "platinum": 30, "quantity": 1, "rank": 10,
      "seller": "PlayerName", "status": "ingame", "reputation": 12,
      "platform": "pc", "crossplay": true, "updated_at": "...",
      "online": true,
      "whisper": "/w PlayerName Hi! I want to buy: \"Serration\" (rank 10) for 30 platinum. (warframe.market)"
    }
  ]
}
```

With `side=all`, sell orders are listed first, so a small `limit` can crowd out buy orders. Sells are ordered cheapest first; buys highest first.

#### `GET /v1/items/{slug}/whispers`
Ready-to-paste in-game whispers.

| Query param | Type | Default | Notes |
| --- | --- | --- | --- |
| `side` | `buy`, `sell` | `buy` | `buy` = buyers you can sell to; `sell` = cheapest sellers |
| `rank` | integer 0-10 | none | |
| `limit` | integer 1-50 | 10 | |
| `platform`, `crossplay` | | | |

```json
{
  "item": "Serration",
  "side": "buy",
  "count": 1,
  "whispers": [
    {"seller": "PlayerName", "platinum": 25, "rank": 10, "status": "online",
     "text": "/w PlayerName Hi! I want to sell: \"Serration\" (rank 10) for 25 platinum. (warframe.market)"}
  ]
}
```

### Errors

| Status | When |
| --- | --- |
| `401` | `WFBOT_API_KEY` is set and the key is missing or wrong |
| `404` | Item not found on warframe.market |
| `422` | Invalid query parameter (FastAPI validation) |
| `502` | warframe.market returned an error or couldn't be reached |
| `503` | warframe.market rate-limited the request; includes `Retry-After: 5` |

### Example

```bash
curl -H "X-API-Key: your-key" \
  "http://127.0.0.1:8000/v1/items/mirage_prime_set/report?rank=0&my_price=60"
```

---

## How the pricing advice works

All logic lives in `market/analysis.py` as pure functions (no network, no Discord), so it is easy to test.

- **Only online/in-game players count.** Offline orders are reported separately for context.
- **Fast price** = the cheapest online ask minus 1p, but never below 1p and never below the best open buy order.
- **Balanced price** = the 25th percentile of online asks (at least the fast price).
- **Patient price** = the median online ask (at least the balanced price).
- **Quick sell** = the best open online buy order.
- **Outlier guard:** if there are at least 3 online sellers and the cheapest ask is below 70% of the second cheapest (and at least 5p lower), it is treated as bait or a mistake and the next price is used as the reference.
- **Market tone** compares buyers to sellers: ratio >= 1 is a seller's market, >= 0.4 is balanced, otherwise a buyer's market. No online sellers or buyers gives "dead market" or "no online competition".
- **Tips** flag mixed ranks, outliers, an instant-sale buyer, a wide spread (over 25% of the lowest ask), crowded markets (8+ sellers), thin supply with active buyers, and asks far above or below the 48-hour median sale price.
- **Price verdicts** (`my_price`): "Priced to sell fast", "Competitive", "Patient pricing", or "Above the typical online ask".
- **Whispers** use warframe.market's standard format: `/w <seller> Hi! I want to buy|sell: "<item>" (rank N) for <price> platinum. (warframe.market)`.

---

## Architecture

```
Discord user ──/wf──> cogs/wiki_commands.py ──> wiki/client.py ──> wiki.warframe.com (MediaWiki API)
                                   │                  │
                                   │                  └─> wiki/parser.py ─> wiki/embeds.py
                                   └─> wiki/ability.py ─> wiki/ability_view.py (dropdown + buttons)

Discord user ──/wm──> cogs/market_commands.py ──> market/client.py  ──> api.warframe.market (read-only)
                                   │            └─> market/analysis.py ─> market/embeds.py
                                   └─> market/seller.py ──> api.warframe.market (authenticated)

HTTP client ──────> main.py / api/main.py (FastAPI) ──> market/client.py + market/analysis.py
```

### Project layout

```
bot.py                     Entry point: intents, aiohttp session, loads cogs, global command sync
main.py                    FastAPI app (see note below; INTEGRATION.md calls it api/main.py)
http_session.py            SessionProvider: reuse or own an aiohttp session, closed on bot.close()
requirements.txt           Python dependencies
.env.example               Template for configuration
INTEGRATION.md             Notes on dropping these modules into the repo
cogs/
  wiki_commands.py         /wf group + autocomplete (loaded by bot.py)
  market_commands.py       /wm group (loaded by bot.py)
  ability_commands.py      Standalone /ability command (NOT loaded by default; /wf ability replaces it)
  client.py, embeds.py,
  analysis.py              Copies of market/client.py, embeds.py and analysis.py (see Known issues)
wiki/
  __init__.py              Exports WikiError, PageNotFoundError, WarframeWikiClient, parse_page, ...
  client.py                MediaWiki API client with caching, concurrency limit
  parser.py                HTML -> WikiPage / InfoboxSection (generic infobox parser)
  ability.py               Ability page fetch + parse + pagination
  ability_view.py          Discord View: overview embed, section dropdown, Prev/Next
  embeds.py                WikiPage -> Discord embeds (warframe, weapon, generic, search, error)
  page_view.py             Overview + section dropdown + pagination for any wiki page
  search_view.py           /wf search: paginated results + dropdown to open one
ui/
  paginator.py             SectionView: shared dropdown + Prev/Next view (wiki and market)
  text.py                  paginate_lines / chunk helpers (no Discord imports)
market/
  client.py                warframe.market v2 client: rate limit, cache, item search, orders, history
  analysis.py              Pricing maths, tips, whispers (pure functions)
  embeds.py                Seller dashboard and whisper embeds
  seller.py                Authenticated writes: sign-in, create/update/delete/list orders, trends, ducats
api/
  __init__.py              (empty; intended home of main.py)
tools/
  inspect_ability.py       Check the ability parser against the live wiki
tests/                     pytest tests (40 test functions across 4 files)
```

### Key design points

- **Slash commands are global.** Never register `/wf` or `/wm` as guild commands: guild-scoped commands cannot appear in DMs or other servers, which breaks user installs. `bot.py` always syncs globally and logs any failure.
- **Integration types and contexts.** Every command is registered for both `guild_install` and `user_install`, in `guild`, `bot_dm` and `private_channel` contexts.
- **One generic wiki parser.** All wiki page types share the `div.group.mw-collapsible` infobox markup, so `wiki/parser.py` handles Warframes, weapons, mods, enemies and more.
- **Ability parsing is heuristic.** It keys off bold/heading labels and the *Warframe Abilities* category rather than exact CSS, so small wiki markup changes are less likely to break it.
- **Defensive market parsing.** `.get()` with fallbacks everywhere, because the warframe.market API has changed shape before.
- **Read vs write separation.** `market/client.py` is read-only; `market/seller.py` owns a separate session and cookie jar so your JWT and CSRF token never mix with shared sessions.
- **Sessions.** `bot.py` creates one shared aiohttp session for the wiki and market clients. The market cog creates its own session and client on first use, and `SessionProvider` is used by the wiki cog.

### Tunable constants

| Where | Value |
| --- | --- |
| `wiki/client.py` | Search cache 300 s, page cache 900 s, 4 concurrent requests, 15 s timeout |
| `market/client.py` | Cache 60 s, 0.34 s minimum gap between requests, item list refreshed every 6 h, history cache 300 s |
| `market/seller.py` | 0.4 s minimum gap between requests |
| `wiki_commands.py` | `AUTOCOMPLETE_LIMIT = 10` |
| `wiki/ability.py` | `PAGE_CHARS = 3800` per embed page |
| `wiki/ability_view.py` | View timeout 600 s |

---

## External APIs used

### WARFRAME Wiki (MediaWiki)

Base URL: `https://wiki.warframe.com/api.php` (all calls `GET`, `format=json`, `formatversion=2`).

| Action | Purpose | Key parameters |
| --- | --- | --- |
| `opensearch` | Autocomplete, `/wf search`, resolving typos and partial names | `search`, `limit`, `namespace=0`, `redirects=resolve` |
| `parse` | Fully rendered page HTML for the parser | `page`, `prop=text` (ability pages: `text\|categories`), `redirects=1` |
| `query` + `prop=categories` | Cheap check: is this page in *Category:Warframe Abilities*? | `titles`, `clcategories`, `redirects=1` |
| `query` + `prop=pageimages` | Ability icon at original size | `titles`, `piprop=original` |

With `formatversion=2`, `parse.text` is a plain HTML string. Errors: HTTP 403 (Cloudflare) and 429 (rate limit) are turned into readable messages; a `missingtitle` API error becomes `PageNotFoundError`.

### warframe.market

Read-only (`market/client.py`, no key needed). Requests send `Language`, `Platform` and `Crossplay` headers.

| Endpoint | Purpose |
| --- | --- |
| `GET https://api.warframe.market/v2/items` | Full item list (cached 6 h) for search and autocomplete |
| `GET https://api.warframe.market/v2/orders/item/{slug}` | Orders for one item |
| `GET https://api.warframe.market/v1/items/{slug}/statistics` | 48-hour sales history (best effort; `None` if unavailable) |

Authenticated (`market/seller.py`, owner only):

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/auth/signin` | Password sign-in (when no `WFM_JWT`) |
| `POST /v2/order` | Create an order |
| `PATCH /v2/order/{id}` | Update an order |
| `DELETE /v2/order/{id}` | Remove an order |
| `POST /v2/order/{id}/close` | Mark an order as sold (`sold_order`, not exposed as a command) |
| `GET /v2/orders/my` | Your orders |
| `GET /v2/orders/recent` | Recent market orders (for `/wm trends`) |
| `GET /v2/items/{slug}`, `/v2/itemId/{id}` | Item details and name lookups |
| `wss://ws.warframe.market/socket` | WebSocket opened after password sign-in |

Item search: exact name or slug scores highest, then prefix, substring, all-words match, and finally typo-tolerant fuzzy matching (`difflib`, cutoff 0.6).

---

## Tests and dev tools

Run the tests from the project root:

```bash
pip install pytest
python -m pytest
```

There are 40 test functions in four files. They use fake sessions, so no network access or Discord token is needed.

| File | Covers |
| --- | --- |
| `tests/test_ability_parser.py` | Ability HTML parsing, sections, stats, image fallback, `clean_text`, pagination, `is_ability_title` |
| `tests/test_http_session.py` | `SessionProvider` session reuse and close behavior |
| `tests/test_market_analysis.py` | Percentiles, price tiers, outlier guard, tone, tips, `assess_price`, whispers, `top_orders`, defensive parsing |
| `tests/test_market_client.py` | Item search ranking and typo tolerance, caching, headers, 404 handling, history, rate limiter |

**Check the ability parser against the live wiki:**

```bash
python -m tools.inspect_ability Nurinarim
python -m tools.inspect_ability "Molecular Prime" --html   # also writes ability_debug.html
```

---

## Full setup guide

### Requirements

- Python **3.12+** (the code needs at least 3.10)
- A Discord account and a Discord application (free)
- Optional: a warframe.market account, for the owner-only listing commands

### 1. Create a Discord application

Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**.

### 2. Create a bot user

In the **Bot** tab, click **Reset Token** and copy the token. No privileged intents need to be enabled.

### 3. Configure the Installation tab

This is the step that's easiest to miss, and the most common reason commands never show up.

1. Open your app, then the **Installation** page.
2. Under **Installation Contexts**, tick **User Install**. Keep **Guild Install** ticked if you also want to add it to servers.
3. Under **Install Link**, choose **Discord Provided Link** (not "None").
4. Under **Default Install Settings**, add scopes for *each* context:
   - **User Install** row: `applications.commands`
   - **Guild Install** row: `applications.commands` and `bot` (no extra permissions needed)
5. Click **Save Changes**.
6. Open the **Install Link** and choose **Add to my apps** (user install) or **Add to server** (guild install).

> Skipping the scopes can make the install "succeed" without permission to register commands, which looks exactly like "the bot runs fine but the slash command never appears."

### 4. Configure `.env`

See [Environment variables](#environment-variables). At minimum, set `DISCORD_TOKEN`. Add `OWNER_ID` and warframe.market credentials only if you want the owner-only listing commands.

### 5. Install and run

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt python-dotenv
.\.venv\Scripts\python bot.py
```

**macOS / Linux**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt python-dotenv
.venv/bin/python bot.py
```

On startup the bot logs the user-install invite URL, the commands it defined locally, and whether the global sync succeeded.

---

## Troubleshooting

**Where are the logs?** `bot.py` writes logs to a file named `log.log` in the working directory (not the console). Open it to follow the checks below. A few debug `print()` calls (autocomplete and cache keys) still go to the console.

### Commands don't show up in Discord

Look in `log.log` for:

| Log line | Meaning |
| --- | --- |
| `Locally defined application commands (N): ...` | The cogs loaded. If it says `<none - cog failed to load!>`, the problem is in the Python code or cog loading. |
| `Synced application commands globally.` | Discord accepted the registration. A `403 Forbidden` instead means **Installation > Default Install Settings** is missing the `applications.commands` scope. Add it, re-run the install link, restart the bot. |

If both look fine but a **user install** still shows nothing (while servers work):

- Never register commands as guild-specific (`guild_ids`, `default_guild_ids`). They can't appear in DMs or other servers.
- Complete both halves: the bot registers commands automatically, but you must also install the app to your account (Install Link, then **Add to my apps**).
- Wait a few minutes; global registration isn't instant.
- Restart Discord with `Ctrl+R`; it caches the command list.
- Try a DM first. Some servers disable **Use External Apps**, which hides user-installed commands there.

### Owner commands say "You're not allowed" or "Listing isn't set up"

- "Not allowed": your Discord user ID is not in `OWNER_ID`.
- "Listing isn't set up": set `WFM_JWT`, or both `WFM_EMAIL` and `WFM_PASSWORD`, then restart.
- Password sign-in can be blocked by Cloudflare; use `WFM_JWT` instead. An expired `WFM_JWT` must be replaced manually.

### "warframe.market rate limit hit" / HTTP 429

The client allows about 3 requests/second. Wait a moment and retry. Over the API this surfaces as `503` with `Retry-After: 5`.

### "WARFRAME Wiki blocked this request" (HTTP 403)

`wiki.warframe.com` sits behind Cloudflare, which can block cloud and datacenter IP ranges. Running the bot from a home/residential connection, or a VPS provider that isn't commonly blocked, usually fixes it.

### The API returns 401

`WFBOT_API_KEY` is set. Send `X-API-Key: <value>`.

### The API ignores my `.env`

uvicorn doesn't read `.env`. Start it with `--env-file .env` or export the variables.

---

## Security notes

- Never commit `.env`. Treat `log.log` as sensitive too (see the first item under Known issues).
- This is a user-installable app, so **anyone** can run its public commands. Owner-only commands check `OWNER_ID`; keep that variable set correctly.
- Prefer `WFM_JWT` to storing your password. Rotate it if it leaks.
- Set `WFBOT_API_KEY` and restrict `WFBOT_CORS` before exposing the API beyond localhost.

---

## Known issues (found while documenting)

These come from reading the source and are listed so they are easy to fix. They are not documented features.

1. **JWT is written to the log.** `market/seller.py` logs `"Token found: " + token` after a password sign-in, so the token ends up in `log.log`. Remove that value from the log line.
2. ~~`/wm update_order` raised a `TypeError`~~ Fixed: the cog now passes `perTrade=per_item`, and unexpected errors are logged and reported. Note that `per_item` is still a boolean option while `/wm list`'s `pertrade` is an integer; check which one warframe.market's API expects.
3. **`requirements.txt` is incomplete.** `python-dotenv` (imported by `bot.py` and `market/seller.py`) is missing, and `pytest` is needed to run the tests. The file also starts with the comment "add these to your existing requirements.txt".
4. **FastAPI file location.** The API source is `main.py` at the repo root, but its docstring and `INTEGRATION.md` say to run `uvicorn api.main:app`, and `api/` only contains `__init__.py`. Either move `main.py` to `api/main.py`, or run `uvicorn main:app`.
5. **Some `/wm` commands aren't owner-gated but use your account.** `/wm ducats_compare` and `/wm trends` go through the authenticated seller session (signing in with your credentials if needed) and can be run by anyone.
6. **Duplicate files.** `cogs/client.py`, `cogs/embeds.py` and `cogs/analysis.py` are copies of the `market/` modules and are not imported anywhere. `cogs/ability_commands.py` defines a standalone `/ability` command that `bot.py` doesn't load (loading it would duplicate `/wf ability`).
7. **Repo clutter.** `INTEGRATION.md` mentions removing `error.log`, `pyvenv.cfg` and `_tmp_*` from git (`git rm --cached ...`) and adding them to `.gitignore`.
8. **Status spoofing on sign-in.** After a password sign-in, `seller.py` opens a WebSocket and sets your warframe.market status to "ingame" for 20 minutes. Make sure that's what you want, as it may not match the site's rules.

---

## Credits and licensing

- Game data comes from the [WARFRAME Wiki](https://wiki.warframe.com) and market data from [warframe.market](https://warframe.market). Check their terms before redistributing content.
- Built with [nextcord](https://docs.nextcord.dev/), [aiohttp](https://docs.aiohttp.org/), [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/) and [FastAPI](https://fastapi.tiangolo.com/).
- This project has no license file yet. Add one (for example MIT) to make clear how others may use the code.


## Multi-user selling (MongoDB)

Listing commands (`list`, `update_order`, `unlist`, `my_orders`) act on the caller's **own** warframe.market account.

1. Set `MONGODB_URI` and `WFM_ENCRYPT_KEY` in `.env` (see `ENVIRONMENT_VARIABLES.txt`).
2. Add Discord ids to `allowed_user_ids.json` (`{"allowed_user_ids": [123, 456]}`). It is re-read when the file changes, and a missing or invalid file allows nobody. Ids in `OWNER_ID` are always allowed.
3. An allowed user runs `/wm link`, fills the private form, and the bot signs in once to check the login. Email and password are stored in the `users` collection, keyed by Discord id. `/wm unlink` deletes them.

Every stored secret is kept as an argon2id hash (checked with `UserStore.verify`) and as a Fernet-encrypted copy. The encrypted copy is needed because signing in to warframe.market takes the real email and password, which a hash cannot return. Anyone with both the database and `WFM_ENCRYPT_KEY` can read the encrypted copies.
