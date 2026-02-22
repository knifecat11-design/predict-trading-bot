# CLAUDE.md

This file provides guidance for AI assistants working on this codebase.

## Project Overview

Cross-platform prediction market arbitrage monitoring system written in Python. It monitors four prediction market platforms (Polymarket, Opinion.trade, Predict.fun, Kalshi) for arbitrage opportunities and sends real-time Telegram notifications when profitable spreads are detected.

**Core business logic:** When `Yes_Price + No_Price < 100%` across different platforms, buying both outcomes simultaneously locks in risk-free profit. The system detects these opportunities automatically.

## Architecture

Two deployable services:

1. **Monitor Bot** (`start_arbitrage.py` → `continuous_monitor.py`): Continuous market scanning with Telegram alerts
2. **Web Dashboard** (`web/dashboard.py`): Flask + SocketIO real-time visualization dashboard (port 5000)

Deployed on **Railway** (PaaS) with Nixpacks builder.

## Directory Structure

```
predict-trading-bot/
├── continuous_monitor.py      # Main monitoring loop and Telegram notifications
├── start_arbitrage.py         # Railway entry point (adds project root to path)
├── src/
│   ├── __init__.py
│   ├── api_client.py          # Predict.fun API client (v1) + MockAPIClient
│   ├── polymarket_api.py      # Polymarket Gamma API client (public, no auth)
│   ├── opinion_api.py         # Opinion.trade API client (SDK + HTTP fallback)
│   ├── kalshi_api.py          # Kalshi public API client (no auth)
│   ├── market_matcher.py      # Cross-platform market matching (inverted keyword index)
│   ├── ws_price_feed.py       # Real-time WebSocket price feeds (Polymarket + Kalshi)
│   └── config_helper.py       # Configuration loading (env vars override YAML)
├── web/
│   ├── dashboard.py           # Flask + SocketIO real-time dashboard
│   └── templates/
│       └── index.html         # Dashboard frontend
├── docs/                      # Documentation (mixed Chinese/English)
├── config.yaml.example        # Full configuration template
├── .env.example               # Environment variable template
├── requirements.txt           # Python dependencies
├── railway.json               # Railway deployment config
└── nixpacks.toml              # Build config (Python 3.11)
```

## Tech Stack

- **Language:** Python 3.11+
- **Web framework:** Flask 3.0+ with Flask-SocketIO 5.3+
- **HTTP:** requests 2.31+
- **WebSocket:** websocket-client 1.6+, websockets 16.0+, simple-websocket (SocketIO transport)
- **Concurrency:** ThreadPoolExecutor (10 workers for price/orderbook fetching)
- **Config:** PyYAML 6.0+, python-dotenv 1.0+
- **Deployment:** Railway with Nixpacks builder
- **Notifications:** Telegram Bot API

## Development Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the monitor bot
python start_arbitrage.py
# or directly:
python continuous_monitor.py

# Run the web dashboard
python web/dashboard.py
```

## Configuration System

Configuration uses a three-tier precedence (highest to lowest):

1. **Environment variables** (used in Railway deployment)
2. **config.yaml** (local development)
3. **Hardcoded defaults** in code

Key environment variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Yes | Target chat ID for notifications |
| `PREDICT_API_KEY` | No | Predict.fun API key |
| `OPINION_API_KEY` | No | Opinion.trade API key |
| `OPINION_BASE_URL` | No | Opinion API endpoint |
| `MIN_ARBITRAGE_THRESHOLD` | No | Minimum spread % to trigger alert (default: 2.0) |
| `SCAN_INTERVAL` | No | Seconds between scans (default: 60) |
| `COOLDOWN_MINUTES` | No | Notification cooldown per market (default: 5) |
| `LOG_LEVEL` | No | Logging verbosity (default: INFO) |
| `PORT` | No | Server port (default: 5000) |
| `SECRET_KEY` | No | Flask secret key |
| `OPINION_POLY_THRESHOLD` | No | Arbitrage threshold % (default: 2.0) |

**Important:** `config.yaml` and `.env` are gitignored. Use the `.example` files as templates.

## Code Conventions

### Naming

- **Functions/variables:** `snake_case` (`get_market_data`, `yes_bid`)
- **Classes:** `PascalCase` (`PolymarketClient`, `PredictAPIClient`)
- **Constants:** `UPPER_SNAKE_CASE` (`TAGS`, `MAX_MARKETS_DISPLAY`)

### Language

- Code identifiers are in English
- Comments and docstrings are in **Chinese** (this is intentional for the target user base)
- Documentation files are a mix of Chinese and English

### Patterns

- **Dataclasses** (`@dataclass`) for data models (`MarketData`, `PolymarketMarket`, `OpinionMarket`)
- **Class-based API clients** with `requests.Session` for connection pooling
- **ThreadPoolExecutor** for concurrent market fetching
- **Thread-safe state** via `threading.Lock()` in the dashboard
- **Graceful degradation:** API clients fall back from SDK to HTTP, and from cached data on error
- **Rate limiting:** Telegram 429 detection with exponential backoff
- **Request timeouts:** 10-15 seconds on all HTTP requests
- **Custom User-Agent headers** on API requests

### Error Handling

- Try-except blocks with `logging` (never bare `except:` without logging)
- Fallback behavior: SDK → HTTP, real → cached data
- Telegram rate-limit detection (HTTP 429) with automatic backoff

### Logging

- Standard library `logging` module
- Format: `%(asctime)s [%(levelname)s] %(name)s: %(message)s`
- UTF-8 output handling for Windows compatibility
- Levels: DEBUG, INFO, WARNING, ERROR

## API Clients

### Polymarket (`src/polymarket_api.py`)
- **Endpoint:** `https://gamma-api.polymarket.com`
- **Auth:** None (public API)
- **Coverage:** ~3,000 active markets across 9 tag categories
- **Caching:** 30-second TTL on market data

### Predict.fun (`src/api_client.py`)
- **Endpoint:** `https://api.predict.fun` (v1 API)
- **Auth:** API key in `X-Api-Key` header
- **Coverage:** ~123 markets
- **Includes:** `MockAPIClient` class for testing without a real key

### Opinion.trade (`src/opinion_api.py`)
- **Endpoint:** `https://proxy.opinion.trade:8443/openapi`
- **Auth:** API key in lowercase `apikey` header (not Authorization or X-API-Key)
- **Coverage:** ~150 markets
- **Rate limit:** 15 req/s
- **Modes:** SDK (full trading with `opinion-clob-sdk`, needs private_key + multi_sig_addr) or HTTP-only (read-only fallback)
- **CRITICAL pricing logic:** Each market has separate Yes and No tokens with independent orderbooks. Use MID PRICE `(bid+ask)/2` from Yes token orderbook, derive No as `1 - yes_mid`. This matches what Opinion.trade website displays. Using raw `yes_bid` or `yes_ask` alone causes extreme price deviations because illiquid markets have very thin bids.

### Kalshi (`src/kalshi_api.py`)
- **Endpoint:** `https://api.elections.kalshi.com/trade-api/v2`
- **Auth:** None (public API)
- **Coverage:** ~4,000 valid priced markets
- **Pagination:** Cursor-based, 1000/page max, rate limit 20 req/s
- **Prices:** Included in `/markets` response (`yes_ask_dollars`, `no_ask_dollars`) — no separate orderbook call needed
- **Important:** Must use `mve_filter=exclude` to skip multivariate sports combo markets (otherwise ~72% of results have zero prices)

## Market Matching (`src/market_matcher.py`)

Two-layer strategy to match identical markets across platforms:

1. **Manual mappings:** Hardcoded market pairs with 100% accuracy
2. **Automatic matching:** Inverted keyword index for O(n+m) cross-platform matching
   - Pre-computes keywords for all B-side markets and builds inverted index
   - Document frequency filtering: tokens in >20% of markets are pruned as noise
   - Early exit: skip expensive SequenceMatcher when keyword score < 0.15
   - Weighted scoring: Entity (40%) + Number/date (30%) + Vocabulary (20%) + String similarity (10%)
   - Hard constraints: Year and price values must match (prevents e.g., "Trump 2024" matching "Trump 2028")
   - Performance: 24x speedup (41s → 1.68s) vs brute-force O(n*m)

## Pricing Model

- All platforms use ASK prices (cost to buy) for arbitrage detection
- **Exception:** Opinion uses MID prices `(bid+ask)/2` because ask-only produces values inconsistent with website
- Cross-platform arbitrage: `Buy Yes on A + Buy No on B < $1.00`
- Same-platform arbitrage: `Buy Yes + Buy No < $1.00` (on same platform)
- Platform fees (all ~2%): deducted to show net profit

## WebSocket Real-time Feeds (`src/ws_price_feed.py`)

- **PolymarketFeed:** `wss://ws-subscriptions-clob.polymarket.com/ws/market`, PING heartbeat every 10s
- **KalshiFeed:** `wss://api.elections.kalshi.com/trade-api/ws/v2`, public ticker channel
- Both run in daemon threads with own asyncio event loops
- Exponential backoff reconnect (2-60s), max 10 retries then stop
- May return 403 in sandbox environments (graceful fallback to polling)

## Dashboard State Management

- `_state` dict protected by `threading.Lock`
- `_scanning` Event prevents overlapping scan cycles
- Arbitrage entries expire after 10 minutes (ARBITRAGE_EXPIRY_MINUTES)
- Price history: in-memory dict, last 30 data points per market, SVG sparkline in UI

## Testing

- No formal test framework (pytest is not in dependencies)
- `MockAPIClient` in `src/api_client.py` provides simulated Predict.fun data
- Hybrid mode (`USE_HYBRID_MODE=true`) allows testing with real Polymarket + mock Predict.fun
- Test files (`test_*.py`) are gitignored

## Deployment

### Railway (Production)
- **Builder:** Nixpacks with Python 3.11
- **Services:** Two separate Railway services sharing the same repo
  - Monitor Bot: runs `start_arbitrage.py`
  - Web Dashboard: runs `web/dashboard.py` on port 5000
- **Config:** All via environment variables in Railway dashboard
- **Restart policy:** Always restart on failure

### Local Development
1. Copy `.env.example` → `.env` and fill in values
2. Optionally copy `config.yaml.example` → `config.yaml`
3. `pip install -r requirements.txt`
4. Run with `python start_arbitrage.py` or `python web/dashboard.py`

## Security Notes

- **Never commit** `config.yaml` or `.env` files (they are gitignored)
- `railway.json` currently contains hardcoded secrets in the `variables` section - these should be moved to Railway's environment variable UI and removed from the file
- API keys and bot tokens must only be set via environment variables or gitignored config files

## Common Pitfalls

1. **Opinion orderbook field naming:** `yes_bid`/`yes_ask` fields are named for the token being queried, not the outcome. When querying the Yes token, `yes_bid` = bid on the Yes token.
2. **Opinion pricing:** Never fetch both Yes and No token orderbooks separately — in illiquid markets the asks are independently high, causing Yes+No sums of 150-200c. Derive No from Yes mid price instead.
3. **Opinion pricing (bid trap):** Never use raw `yes_bid` to derive No price (`1 - yes_bid`). In illiquid markets, `yes_bid` can be near 0, making No price ~99.9c. Always use mid price `(bid+ask)/2`.
4. **Polymarket outcomePrices:** It's a JSON string, not a list. Must `json.loads()` it.
5. **Predict.fun pagination:** Uses `cursor` response field but `after` query parameter.
6. **Kalshi MVE filter:** Without `mve_filter=exclude`, ~72% of returned markets are zero-priced multivariate events.

## Git Workflow

- Feature branches follow the pattern `claude/review-trading-bot-*`
- PRs are merged to main via GitHub pull requests
- Commit messages use conventional-style prefixes: `feat:`, `fix:`, `refactor:`, `debug:`
