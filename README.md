# WarframeBOT

**A Discord companion for Warframe players and traders.**

WarframeBOT brings the [WARFRAME Wiki](https://wiki.warframe.com/) and [warframe.market](https://warframe.market/) into Discord through slash commands. Look up Warframes, weapons, abilities, and other wiki pages, or analyze market orders to make more informed trading decisions.

Built with Python, Nextcord, aiohttp, and FastAPI, the project includes a Discord application, a reusable market client, pure market-analysis functions, and an optional REST API.

## Features

- **Wiki lookups:** Search Warframes, weapons, abilities, mods, enemies, relics, quests, and other wiki pages.
- **Market price analysis:** Examine online sellers, buyers, price distributions, and the gap between the highest buy order and lowest sell order.
- **Seller recommendations:** Compare quick-sale, balanced, and patient pricing strategies.
- **Buyer and seller lists:** Find potential buyers and competing sellers, with ready-to-copy in-game whisper messages.
- **Rank filtering:** Analyze applicable mods and arcanes at a specific rank.
- **Price history:** Include historical sales data when the upstream statistics endpoint is available.
- **Item autocomplete:** Find tradable items and wiki pages without memorizing exact names or slugs.
- **REST API:** Expose market reports, filtered orders, and whisper suggestions through FastAPI.

## Commands

### `/wf` — WARFRAME Wiki

| Command | Description |
| --- | --- |
| `/wf warframe` | Look up a Warframe's stats, passive, and abilities. |
| `/wf ability` | Look up ability stats, mechanics, kit interactions, and tips. |
| `/wf weapon` | View weapon stats and damage information. |
| `/wf page` | Look up any wiki page. |
| `/wf search` | Search for matching wiki pages. |

Wiki lookups use the public MediaWiki API, with parsed information presented in Discord embeds.

### `/wm` — warframe.market

The market commands are designed primarily for **players who want to sell items and make better pricing decisions**.

| Command | Description |
| --- | --- |
| `/wm price` | Generate a seller-focused pricing report, including price distribution, online buyers and sellers, recommended listing prices, and available historical context. |
| `/wm buyers` | Find online buyers and generate ready-to-copy whispers. |
| `/wm sellers` | Inspect the cheapest online sellers and your competition. |

Market commands support the following options where applicable:

- **Item:** Search by item name using autocomplete.
- **Rank:** Filter rank-specific items such as mods and arcanes. Omit the option to include all ranks.
- **Platform:** Select PC, PlayStation, Xbox, or Switch.
- **Your price:** On `/wm price`, optionally enter a planned or current price to see how it compares with the market.

Only orders from online or in-game players are treated as immediately actionable. Offline orders are excluded from the headline pricing recommendations.

### Understanding the price report

The market analysis engine calculates several useful metrics:

- **Lowest and second-lowest sell prices:** Identify the current cheapest listings and potential outliers.
- **Price distribution:** Examine the 25th percentile, median, 75th percentile, and mean of online sell prices.
- **Highest buy price:** See the strongest currently available buy order.
- **Spread:** Compare the lowest online sell price against the highest online buy price.
- **Suggested listing prices:** Compare quick-sale, balanced, and patient pricing strategies.
- **Price ladder:** See how many competing orders exist at the cheapest price levels.
- **Rank breakdown:** Inspect market conditions across available ranks.
- **Historical context:** Include recent sales statistics when available.

These figures are estimates derived from available orders, not guarantees of sale price or trading speed.

## How it works

### Wiki integration

The wiki client uses the public MediaWiki API instead of relying entirely on direct page scraping.

It searches page titles, retrieves rendered page content, and parses relevant information into structured page data. Dedicated embed builders format the results for Discord.

### Market integration

The market client communicates with the public [warframe.market API](https://warframe.market/).

- **API v2:** Used for the item catalogue and live order data.
- **API v1:** Used for historical statistics where the required endpoint remains available.

The client normalizes API responses into item and order models. The analysis module then turns those models into seller reports, price recommendations, filtered order lists, and whisper text.

The market client also provides:

- Asynchronous HTTP requests.
- Request pacing.
- In-memory response caching.
- Defensive parsing of API responses.
- Item-name and slug resolution.
- Error handling for missing items and upstream failures.

Historical data is best-effort: if the legacy statistics endpoint is unavailable, live order analysis can still be performed.

### Shared market analysis

The functions in `market/analysis.py` are designed to be independent of Discord and network requests. This allows market calculations to be tested separately and reused by the Discord commands and REST API.

## Installation

### Requirements

- Python 3.12 or newer.
- A Discord application and bot token.
- Internet access to the WARFRAME Wiki and warframe.market.

No API key is required for the public, read-only wiki or market endpoints.

### 1. Create a Discord application

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create a new application.
3. Open the **Bot** tab and create or configure the bot user.
4. Copy the bot token and keep it private.

### 2. Configure installation contexts

To use the app as a Discord User Install application:

1. Open your application's **Installation** page.
2. Enable **User Install**. Enable **Guild Install** too if you want traditional server installation.
3. Configure the default installation scopes:
   - User Install: `applications.commands`
   - Guild Install: `applications.commands` and `bot`
4. Ensure the installation link is enabled and save your changes.
5. Use the generated link to add the application to your account or server.

Global slash-command registration and installing the app are separate steps. Both must be completed for commands to appear in the intended context.

### 3. Clone the repository

```bash
git clone https://github.com/0xsint/My-Warframe-Bot.git
cd My-Warframe-Bot
```

### 4. Create a virtual environment

**Windows PowerShell**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Linux / macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 5. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 6. Configure environment variables

Create a `.env` file in the project root, using `.env.example` if provided:

```dotenv
DISCORD_TOKEN=your-discord-bot-token
```

Do not commit your `.env` file or expose your bot token.

### 7. Run the Discord bot

```bash
python bot.py
```

Check the startup logs to confirm that the command cogs loaded and the global application commands were registered successfully.

## REST API

The project includes a FastAPI application in `api/main.py` for accessing market information outside Discord.

**Status:** The REST API currently requires a small code correction before it can start. `api/main.py` imports and instantiates `MarketClient`, but `market/client.py` defines `WarframeMarketClient`. Correct those references before running the API.

Once corrected, the intended startup command is:

```bash
uvicorn api.main:app --reload
```

The interactive API documentation is available at `http://127.0.0.1:8000/docs`.

### Endpoints

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/health` | Basic health check. |
| `GET` | `/v1/items/search` | Search the tradable item catalogue. |
| `GET` | `/v1/items/{slug}/report` | Get a seller-focused pricing report. |
| `GET` | `/v1/items/{slug}/orders` | Retrieve filtered market orders. |
| `GET` | `/v1/items/{slug}/whispers` | Generate ready-to-copy whisper suggestions. |

### Example requests

Search for an item:

```http
GET /v1/items/search?q=Mirage%20Prime
```

Retrieve a pricing report:

```http
GET /v1/items/mirage_prime_set/report?platform=pc
```

Retrieve online sell orders:

```http
GET /v1/items/mirage_prime_set/orders?side=sell&online_only=true
```

Generate buyer whispers:

```http
GET /v1/items/mirage_prime_set/whispers?side=buy&limit=10
```

The API supports platform selection, crossplay settings, and rank filtering on applicable endpoints. Consult `/docs` for the full parameter definitions.

### API authentication and configuration

By default, the API-key middleware does not require a key. To enable protection, set:

```dotenv
WFBOT_API_KEY=your-private-api-key
```

Clients must then send:

```http
X-API-Key: your-private-api-key
```

The service also supports a `WFBOT_CORS` environment variable containing comma-separated allowed origins. Its current default permits all origins, so configure this appropriately before exposing the service publicly.

**Security note:** Do not expose a development server directly to the public internet. Use an appropriate production server, access controls, and deployment configuration.

## Testing

The repository includes offline tests for market parsing and analysis.

Run the test suite from the project root:

```bash
python -m unittest discover -s tests -v
```

The tests are intended to validate local behavior without requiring live market data. A passing test suite does not guarantee that upstream APIs have not changed.

## Project structure

```text
My-Warframe-Bot/
├── api/
│   └── main.py             # FastAPI market endpoints
├── cogs/
│   ├── wiki_commands.py    # Wiki slash commands
│   └── market_commands.py # Market slash commands
├── market/
│   ├── client.py           # HTTP client, caching, and item/order models
│   ├── analysis.py         # Price calculations and market reports
│   └── embeds.py           # Market report and whisper embeds
├── wiki/
│   ├── client.py           # Wiki API client
│   ├── parser.py           # Wiki page parsing
│   └── embeds.py            # Wiki embed builders
├── tests/                  # Automated tests
├── tools/                  # Supporting utilities
├── bot.py                  # Discord bot entry point
├── http_session.py         # HTTP session management
├── requirements.txt        # Python dependencies
└── README.md
```

## Troubleshooting

### Slash commands do not appear

- Check the startup logs for cog-loading errors.
- Verify the Discord Installation page and installation scopes.
- Confirm the commands were registered globally.
- Make sure the application has been installed in the context where you are trying to use it.
- Restart Discord to refresh its cached command list.

### Market requests fail

- Check the logs for HTTP status codes and upstream errors.
- Retry after a temporary rate-limit response.
- Confirm that your host can reach `api.warframe.market`.
- HTTP 403 responses may indicate an upstream bot-protection challenge or a blocked hosting IP.

### Autocomplete returns no results

- Confirm the market item catalogue has finished loading.
- Try a more specific item name.
- Check the logs for API errors.
- Remember that autocomplete only displays a limited number of choices.

### Historical prices are missing

Historical statistics rely on the legacy v1 endpoint. If that endpoint fails or returns unavailable data, the bot may still provide live order analysis without historical context.

## Contributing

Contributions, bug reports, and suggestions are welcome.

When reporting a problem, include the command or endpoint involved, the expected behavior, the actual result, and relevant error logs. Remove tokens and other sensitive information before posting logs.

For code contributions, keep API access separate from market calculations where possible, preserve defensive error handling, and add tests for changes to price calculations or response parsing.

## Disclaimer

WarframeBOT is an unofficial community project. It is not affiliated with, endorsed by, or sponsored by Digital Extremes Ltd.

WARFRAME and related intellectual property belong to their respective owners. Market data is provided for informational purposes and can change at any time. Always verify listings before trading.

**Project:** [0xsint/My-Warframe-Bot](https://github.com/0xsint/My-Warframe-Bot)  
**Wiki:** [wiki.warframe.com](https://wiki.warframe.com/)  
**Market:** [warframe.market](https://warframe.market/)  
**Framework:** [Nextcord](https://docs.nextcord.dev/) · [FastAPI](https://fastapi.tiangolo.com/)
