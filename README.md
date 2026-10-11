# WarframeBOT

A Discord **user-installable app** written in [nextcord](https://docs.nextcord.dev/)
that scrapes the official [WARFRAME Wiki](https://wiki.warframe.com) (via its
public MediaWiki API) to answer `/wf` slash commands about Warframes,
weapons, and any other wiki page.

Because it's registered as a **User Install** app, anyone can add it to
*their own* Discord account (instead of a specific server) and then use its
commands in any server, DM, or group DM — even ones this bot was never
invited to.

## Commands

All commands live under the `/wf` group:

| Command | Description |
| --- | --- |
| `/wf warframe name:<Warframe>` | Health/Shields/Armor/Energy/Sprint Speed, Passive, and ability names. |
| `/wf weapon name:<Weapon>` | Mastery rank, slot, accuracy, crit/status, damage, and per-fire-mode stats. |
| `/wf page name:<any page>` | Generic lookup for mods, enemies, relics, quests, factions, etc. |
| `/wf search query:<text>` | Lists matching wiki page titles with links. |

`name` options have live autocomplete powered by the wiki's search API.

## How it works

The wiki runs on MediaWiki, so instead of fragile HTML scraping this bot
calls the built-in API:

* `action=opensearch` — fuzzy title search (used for autocomplete and to
  resolve typos/partial names).
* `action=parse` — fully rendered page HTML, which is then parsed with
  BeautifulSoup. Every page type (Warframe, weapon, mod, enemy, ...) reuses
  the same `div.group.mw-collapsible` "infobox" widget markup, so one
  generic parser (`wiki/parser.py`) extracts structured stats for all of
  them.

Results are cached in memory for a few minutes to keep repeat lookups and
autocomplete snappy and to be polite to the wiki.

## Setup

1. **Create a Discord application**: go to the
   [Discord Developer Portal](https://discord.com/developers/applications) →
   *New Application*.
2. **Create a bot user**: in the *Bot* tab, click *Reset Token* and copy it.
3. **Configure the Installation tab** — this is the step that's easy to miss
   and the most common reason `/wf` never shows up even with correct code:
   * Open your app → **Installation** page.
   * Under **Installation Contexts**, tick **User Install** (and keep
     **Guild Install** ticked too if you also want to add it to servers).
   * Under **Install Link**, make sure the dropdown is set to
     **Discord Provided Link** (not "None") — this is what generates a
     working install URL at all.
   * Under **Default Install Settings**, add scopes for *each* context you
     enabled:
     * **User Install** row → add the `applications.commands` scope.
     * **Guild Install** row → add `applications.commands` **and** `bot`
       scopes (pick whatever bot permissions you want, e.g. nothing more
       than "Send Messages" — this bot only ever responds inside
       interactions, so it needs no special permissions).
   * Click **Save Changes**.
   * Copy the **Install Link** shown on that same page and open it in a
     browser. Choose **Add to my apps** to install it to your own account
     (user install), or **Add to server** to add it to a server (guild
     install). You can do both with the same link.

   If you skip the scopes under *Default Install Settings*, the install
   flow can "succeed" without actually granting the app permission to
   register commands for that context — which looks exactly like "the bot
   runs fine, but the slash command never appears."
4. **Configure `.env`**: copy `.env.example` to `.env` (or edit the existing
   `.env`) and paste in your token:

   ```
   DISCORD_TOKEN=your-bot-token-here
   ```

5. **Install dependencies** (Python 3.12+):

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\pip install -r requirements.txt
   ```

6. **Run the bot**:

   ```powershell
   .\.venv\Scripts\python bot.py
   ```

   On startup it logs an invite URL — open it to add the app to your own
   account (user install) so you can use `/wf` anywhere.

## Troubleshooting: `/wf` doesn't show up in Discord

Check the bot's console output on startup — it now logs exactly what it's
doing, which narrows this down fast:

* `Locally defined application commands (1): wf` — confirms the cog loaded
  and the command exists in code. If this says `<none - cog failed to load!>`
  instead, the bug is in the Python code/cog loading, not Discord config.
* `Synced application commands globally.` — confirms Discord *accepted* the
  registration. If you instead see a `403 Forbidden` error here, the app's
  **Installation** → **Default Install Settings** is missing the
  `applications.commands` scope for the context you installed it in (see
  step 3 above) — add it, re-run the install link, and restart the bot.
* If both of the above look fine but `/wf` *still* doesn't appear for a
  **user install** specifically (while working fine in a server):
  * **Never register `/wf` as a guild-specific command.** This bot always
    syncs it globally on purpose. A command registered to one specific
    guild (e.g. via `guild_ids=[...]` or a `default_guild_ids` on the Bot)
    only ever exists in that one guild — it cannot appear in DMs, group
    DMs, or any other server no matter what `integration_types`/`contexts`
    it has, since those fields only affect *global* commands. If you
    previously saw this bot behave this way with a `TEST_GUILD_ID` env var,
    that's why — it's been removed.
  * Make sure you actually completed **both** halves of the install flow:
    registering the command with Discord (automatic, done by the bot on
    startup) and *installing the app to your account* (Installation page →
    Install Link → open it → **Add to my apps**). These are independent
    steps and both are required.
  * Give it a few minutes — global command registration isn't always
    instant, even though it's usually much faster than Discord's quoted
    worst case of ~1 hour.
  * Restart your Discord client (Ctrl+R) — it caches the command list.
  * Try it in a DM with the bot/app itself first; some servers disable the
    "Use External Apps" permission for channels, which hides user-installed
    commands there even though they work everywhere else.

## A note on Cloudflare

`wiki.warframe.com` sits behind Cloudflare, which can block requests from
some cloud/datacenter IP ranges with an HTTP 403 "challenge" response. This
bot sends a descriptive `User-Agent` and surfaces a clear error message if
that happens, but if you consistently get "WARFRAME Wiki blocked this
request" errors, try running the bot from a residential network or a VPS
provider that isn't commonly Cloudflare-blocked.

## Project layout

```
bot.py                   Entry point: bot lifecycle, aiohttp session, intents
cogs/
  wiki_commands.py        /wf slash command group + autocomplete
wiki/
  client.py                Async MediaWiki API client + caching
  parser.py                HTML -> structured WikiPage/InfoboxSection data
  embeds.py                WikiPage -> Discord embeds
requirements.txt
.env / .env.example
```
