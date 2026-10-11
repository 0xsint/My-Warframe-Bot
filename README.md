# WarframeBOT

A Discord **user-installable app** written in [nextcord](https://docs.nextcord.dev/) that answers Warframe questions from two sources:

- **`/wf`**: scrapes the official [WARFRAME Wiki](https://wiki.warframe.com) (via its public MediaWiki API) for Warframes, weapons, and any other wiki page.
- **`/wfm`**: checks live prices, availability, set deals and price trends on [warframe.market](https://warframe.market) (via its public API).

Because it's registered as a **User Install** app, anyone can add it to *their own* Discord account (instead of a specific server) and then use its commands in any server, DM, or group DM — even ones this bot was never invited to.

## Commands

### `/wf` — Wiki lookups

| Command                        | Description                                                                 |
| ------------------------------ | --------------------------------------------------------------------------- |
| `/wf warframe name:<Warframe>` | Health/Shields/Armor/Energy/Sprint Speed, Passive, and ability names.       |
| `/wf weapon name:<Weapon>`     | Mastery rank, slot, accuracy, crit/status, damage, and per-fire-mode stats. |
| `/wf page name:<any page>`     | Generic lookup for mods, enemies, relics, quests, factions, etc.            |
| `/wf search query:<text>`      | Lists matching wiki page titles with links.                                 |

`name` options have live autocomplete powered by the wiki's search API.

### `/wfm` — warframe.market prices

| Command | Description |
| ------- | ----------- |
| `/wfm price item:<item>` | Live price check: cheapest online sellers, top buyers, the spread, the median of the 5 cheapest asks, units for sale, and the 7-day average sold price. Flags an **instant flip** when an online buyer bids at or above the cheapest ask. |
| `/wfm compare item1 item2 [item3] [item4]` | Side-by-side table of 2–4 items, sorted cheapest first, with the cheapest highlighted. |
| `/wfm available item [max_price] [quantity]` | Is it for sale right now? With a budget and/or quantity it finds the cheapest combination of sellers and gives copy-paste `/w` whispers. |
| `/wfm set item:<set or part>` | Compares a Prime set's price against buying its parts, and shows the margin for assembling a set or breaking one up. |
| `/wfm trend item:<item>` | Closed-trade history: sparkline, 7-day / 30-day averages, week-over-week change, 90-day low/high, and volume. |

Every `/wfm` command also accepts:

- `rank`: for mods and arcanes, include **any** rank, **unranked** only, or **max rank** only. It has no effect on items that can't be ranked.
- `platform`: PC (default), PlayStation, Xbox, Switch, or Mobile.

Only **online** orders (in-game or online) are counted, since offline sellers won't answer a whisper. `item` options have live autocomplete, and you can also type a name or slug directly (for example `ash prime set` or `ash_prime_set`). If a typed name matches several items the bot lists the candidates rather than guessing.

## How it works

### Wiki (`/wf`)

The wiki runs on MediaWiki, so instead of fragile HTML scraping this bot calls the built-in API:

- `action=opensearch`: fuzzy title search (used for autocomplete and to resolve typos/partial names).
- `action=parse`: fully rendered page HTML, which is then parsed with BeautifulSoup. Every page type (Warframe, weapon, mod, enemy, ...) reuses the same `div.group.mw-collapsible` "infobox" widget markup, so one generic parser (`wiki/parser.py`) extracts structured stats for all of them.

Results are cached in memory for a few minutes to keep repeat lookups and autocomplete snappy and to be polite to the wiki.

### Market (`/wfm`)

warframe.market has a public JSON API, so there's no scraping here either:

- **v2 API** for the item catalogue (`/v2/items`), live orders (`/v2/orders/item/{slug}`) and set contents (`/v2/item/{slug}/set`).
- **v1 API** for price history (`/v1/items/{slug}/statistics`), which has no v2 equivalent yet. If that endpoint ever goes away, `/wfm trend` will say so and `/wfm price` will simply omit its 7-day average line.

The client (`market/client.py`) is built to be a good API citizen:

- **Rate limited** to roughly 3 requests per second, shared across all commands.
- **Cached**: live orders for 60 seconds, price history for 10 minutes, the item list and set contents for 6 hours. Identical requests made at the same moment share one network call.
- **Retries** with backoff on `429` and `5xx` responses (honouring `Retry-After`). If an item-list refresh fails, the last good copy keeps autocomplete working.
- Reuses the bot's shared `aiohttp` session, but sends its **own `User-Agent`** (see [Configuration](#configuration)).

Analysis (`market/analysis.py`) is pure functions with no network access, so the price maths, set breakdowns and trends are unit-tested offline.

## Setup

1. **Create a Discord application**: go to the [Discord Developer Portal](https://discord.com/developers/applications) → *New Application*.

2. **Create a bot user**: in the *Bot* tab, click *Reset Token* and copy it.

3. **Configure the Installation tab** — this is the step that's easy to miss and the most common reason `/wf` never shows up even with correct code:

   - Open your app → **Installation** page.
   - Under **Installation Contexts**, tick **User Install** (and keep **Guild Install** ticked too if you also want to add it to servers).
   - Under **Install Link**, make sure the dropdown is set to **Discord Provided Link** (not "None") — this is what generates a working install URL at all.
   - Under **Default Install Settings**, add scopes for *each* context you enabled:
     - **User Install** row → add the `applications.commands` scope.
     - **Guild Install** row → add `applications.commands` **and** `bot` scopes (pick whatever bot permissions you want, e.g. nothing more than "Send Messages" — this bot only ever responds inside interactions, so it needs no special permissions).
   - Click **Save Changes**.
   - Copy the **Install Link** shown on that same page and open it in a browser. Choose **Add to my apps** to install it to your own account (user install), or **Add to server** to add it to a server (guild install). You can do both with the same link.

   If you skip the scopes under *Default Install Settings*, the install flow can "succeed" without actually granting the app permission to register commands for that context — which looks exactly like "the bot runs fine, but the slash command never appears."

4. **Configure `.env`**: copy `.env.example` to `.env` (or edit the existing `.env`) and paste in your token:

   ```
   DISCORD_TOKEN=your-bot-token-here
   ```

   No API key is needed for warframe.market or the wiki.

5. **Install dependencies** (Python 3.12+):

   ```
   python -m venv .venv
   .\.venv\Scripts\pip install -r requirements.txt
   ```

6. **Run the bot**:

   ```
   .\.venv\Scripts\python bot.py
   ```

   On startup it logs an invite URL — open it to add the app to your own account (user install) so you can use `/wf` and `/wfm` anywhere.

## Configuration

The market client is created in `bot.py`:

```python
self.market = WarframeMarketClient(self._http_session)
```

It accepts a few optional arguments:

| Argument | Default | Purpose |
| -------- | ------- | ------- |
| `platform` | `"pc"` | Default platform when a command doesn't specify one. |
| `language` | `"en"` | Language used for item names. |
| `crossplay` | `True` | Include crossplay orders. |
| `min_interval` | `0.35` | Minimum seconds between API requests (about 3 per second). Don't lower this much. |
| `max_retries` | `3` | Attempts per request on `429`/`5xx`/network errors. |
| `user_agent` | `WarframeBOT-market/0.1 (...)` | Sent on every market request. Set it to something that identifies your bot and how to reach you, or pass `None` to use the session's User-Agent instead. |

## Troubleshooting: `/wf` doesn't show up in Discord

Check the bot's console output on startup — it logs exactly what it's doing, which narrows this down fast:

- `Locally defined application commands (1): wf` — confirms the cog loaded and the command exists in code. With the market cog loaded you should see **both** `wf` and `wfm` listed. If a command is missing or this says `<none - cog failed to load!>` instead, the bug is in the Python code/cog loading, not Discord config.
- `Synced application commands globally.` — confirms Discord *accepted* the registration. If you instead see a `403 Forbidden` error here, the app's **Installation** → **Default Install Settings** is missing the `applications.commands` scope for the context you installed it in (see step 3 above) — add it, re-run the install link, and restart the bot.
- If both of the above look fine but a command *still* doesn't appear for a **user install** specifically (while working fine in a server):
  - **Never register these commands as guild-specific.** This bot always syncs them globally on purpose. A command registered to one specific guild (e.g. via `guild_ids=[...]` or a `default_guild_ids` on the Bot) only ever exists in that one guild — it cannot appear in DMs, group DMs, or any other server no matter what `integration_types`/`contexts` it has, since those fields only affect *global* commands. If you previously saw this bot behave this way with a `TEST_GUILD_ID` env var, that's why — it's been removed.
  - Make sure you actually completed **both** halves of the install flow: registering the command with Discord (automatic, done by the bot on startup) and *installing the app to your account* (Installation page → Install Link → open it → **Add to my apps**). These are independent steps and both are required.
  - Give it a few minutes — global command registration isn't always instant, even though it's usually much faster than Discord's quoted worst case of ~1 hour.
  - Restart your Discord client (Ctrl+R) — it caches the command list.
  - Try it in a DM with the bot/app itself first; some servers disable the "Use External Apps" permission for channels, which hides user-installed commands there even though they work everywhere else.

## Troubleshooting: warframe.market errors

**"warframe.market refused the request (HTTP 403)"**: warframe.market sits behind bot protection that can block some IP ranges (especially cloud/VPS hosts) or unusual requests. When this happens the bot logs a line starting `HTTP 403 from ...` with the `Server`, `CF-RAY` and `cf-mitigated` headers and the start of the response body, which tells you what kind of block it is. To narrow it down, run this on the machine hosting the bot:

```
curl -i -H "Platform: pc" -H "Language: en" https://api.warframe.market/v2/items
```

- **curl also gets a 403**: your IP is being blocked. Run the bot from a different network or host (a residential connection usually works).
- **curl returns JSON but the bot gets a 403**: the problem is the request, not the IP. Set a descriptive `user_agent` (see [Configuration](#configuration)).
- **curl returns an HTML "Just a moment…" page**: it's a Cloudflare challenge, which a plain HTTP client can't pass. Change IP/host, or ask the warframe.market team about getting a documented bot allowlisted.

**"warframe.market is not responding"**: the API timed out or kept returning `429`/`5xx` after several retries. Try again shortly. If it happens constantly, make sure nothing else is sending requests from the same IP.

**Prices look empty or wrong**: the parser reads the v2 response shape and tolerates minor variations, but if the API changes, check one raw `/v2/orders/item/{slug}` response against `Order.from_api` in `market/client.py`. Also remember only *online* orders are counted, so a rarely traded item can legitimately show "No sellers online."

**Autocomplete is empty right after startup**: the item list (a few thousand items) is fetched once at launch. Give it a few seconds; it's cached for 6 hours afterwards.

## A note on Cloudflare

Both sites this bot talks to sit behind Cloudflare, which can block requests from some cloud/datacenter IP ranges.

- `wiki.warframe.com` may return an HTTP 403 "challenge" response. The wiki client sends a descriptive `User-Agent` and surfaces a clear error message ("WARFRAME Wiki blocked this request") if that happens.
- `api.warframe.market` can do the same; see the market troubleshooting section above.

If you consistently see these errors, try running the bot from a residential network or a VPS provider that isn't commonly blocked.

## Tests

The market code has an offline test suite (no network, Discord or API key needed; `aiohttp`/`nextcord` are stubbed if not installed):

```
python -m unittest discover -s tests -v
```

It covers response parsing, rate limiting, retries, caching, item-name resolution, the purchase planner, set/part maths, trend calculations and embed size limits. It does **not** hit the live API, so a passing run doesn't guarantee warframe.market hasn't changed its responses.

## Project layout

```
bot.py                   Entry point: bot lifecycle, aiohttp session, intents
cogs/
  wiki_commands.py        /wf slash command group + autocomplete
  market_commands.py      /wfm slash command group + autocomplete
wiki/
  client.py                Async MediaWiki API client + caching
  parser.py                HTML -> structured WikiPage/InfoboxSection data
  embeds.py                WikiPage -> Discord embeds
market/
  client.py                Async warframe.market client: rate limit, cache, retries, item index
  analysis.py              Pure price maths: summaries, purchase plans, set breakdowns, trends
  embeds.py                Analysis results -> Discord embeds
tests/
  test_market.py           Offline tests for the market package
requirements.txt
.env / .env.example
```
