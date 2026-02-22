"""
Cross-platform prediction market arbitrage dashboard
Platforms: Polymarket, Opinion.trade, Predict.fun, Kalshi

v3.2: + Kalshi platform, price history tracking, inverted-index matching
"""

import os
import sys
import json
import time
import logging
import threading
import traceback
from datetime import datetime, timedelta
from typing import Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO, emit

# Setup logging first
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Add project root to path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logger.info(f"Project root: {PROJECT_ROOT}")

app = Flask(__name__,
            template_folder=os.path.join(os.path.dirname(__file__), 'templates'))
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'arb-monitor-ws-key')

# SocketIO with threading mode + simple-websocket for true WebSocket
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading',
                    logger=False, engineio_logger=False)
_ws_clients = 0  # Track connected WebSocket clients

# ============================================================
# Configuration constants - tune these to control resource usage
# ============================================================
MAX_MARKETS_DISPLAY = 15          # Max markets to store in state per platform (for UI)
MAX_ARBITRAGE_DISPLAY = 30        # Max arbitrage opportunities to keep
ARBITRAGE_EXPIRY_MINUTES = 10     # Remove stale arbitrage after N minutes
OPINION_DETAILED_FETCH = 100      # Individual No price fetches (concurrent)
POLYMARKET_FETCH_LIMIT = 5000     # Polymarket markets to fetch (total active ~28k)
OPINION_MARKET_LIMIT = 500        # Total opinion markets to process
OPINION_PARSED_LIMIT = 400        # Max parsed opinion markets
PREDICT_EXTREME_FILTER = 0.03     # Filter markets with Yes < 3% or > 97%
PRICE_FETCH_WORKERS = 10          # Concurrent threads for price/orderbook fetching
MIN_SCAN_INTERVAL = 45            # Minimum seconds between scans (prevents overload)
KALSHI_FETCH_LIMIT = 5000         # Kalshi markets to fetch (all open markets)
PRICE_HISTORY_MAX_POINTS = 30     # Max price history data points per market

# Platform fee rates (used for net profit calculation)
PLATFORM_FEES = {
    'polymarket': 0.02,    # 2% taker fee
    'opinion': 0.02,       # 2% estimated
    'predict': 0.02,       # feeRateBps: 200 = 2%
    'kalshi': 0.02,        # ~2% effective (Kalshi: 7% of profit ≈ 2% of trade value)
}

# Global state
_state = {
    'platforms': {
        'polymarket': {'status': 'unknown', 'markets': [], 'last_update': 0, 'count': 0},
        'opinion': {'status': 'unknown', 'markets': [], 'last_update': 0, 'count': 0},
        'predict': {'status': 'unknown', 'markets': [], 'last_update': 0, 'count': 0},
        'kalshi': {'status': 'unknown', 'markets': [], 'last_update': 0, 'count': 0},
    },
    'arbitrage': [],
    'scan_count': 0,
    'started_at': datetime.now().isoformat(),
    'threshold': 2.0,
    'last_scan': '-',
    'error': None,
    'price_history': {},  # market_key → [{timestamp, arbitrage, net_profit}, ...]
}
_lock = threading.Lock()
_scanning = threading.Event()  # Scan guard: prevents overlapping scans

# Real-time WebSocket price feed (initialized in background_scanner)
_realtime_feed = None
# In-memory price cache updated by WS (platform → market_id → {yes_ask, no_ask})
_ws_prices: Dict[str, Dict[str, Dict[str, float]]] = {}
_ws_update_count = 0


def load_config():
    """Load config"""
    config = {}

    # Try environment variables first
    config['opinion'] = {
        'api_key': os.getenv('OPINION_API_KEY', ''),
        'base_url': os.getenv('OPINION_BASE_URL', 'https://proxy.opinion.trade:8443/openapi'),
    }
    config['api'] = {
        'api_key': os.getenv('PREDICT_API_KEY', ''),
        'base_url': os.getenv('PREDICT_BASE_URL', 'https://api.predict.fun'),
    }
    config['opinion_poly'] = {
        'min_arbitrage_threshold': float(os.getenv('OPINION_POLY_THRESHOLD', 2.0)),
        'min_confidence': 0.2,
    }
    config['arbitrage'] = {
        'scan_interval': max(MIN_SCAN_INTERVAL, int(os.getenv('SCAN_INTERVAL', 60))),
    }

    # Try to load from config file
    try:
        import yaml
        config_path = os.path.join(PROJECT_ROOT, 'config.yaml')
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                file_config = yaml.safe_load(f)
                # Merge with env vars (env vars take precedence)
                if file_config.get('opinion', {}).get('api_key') and not config['opinion']['api_key']:
                    config['opinion']['api_key'] = file_config['opinion']['api_key']
                if file_config.get('api', {}).get('api_key') and not config['api']['api_key']:
                    config['api']['api_key'] = file_config['api']['api_key']
            logger.info("Loaded config from config.yaml")
    except Exception as e:
        logger.warning(f"Could not load config.yaml: {e}")

    return config


def strip_html(html_text):
    """Strip HTML tags from text, return plain text"""
    if not html_text:
        return ''
    import re
    return re.sub(r'<[^>]+>', '', html_text).strip()


def slugify(text):
    """Convert text to URL-friendly slug format (improved for Predict.fun)"""
    import re
    text = text.lower()

    text = re.sub(r'\bequal\s+(to\s+)?(or\s+)?greater\s+than\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bgreater\s+(than\s+)?(or\s+)?equal\s+(to\s+)?\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bless\s+(than\s+)?(or\s+)?equal\s+(to\s+)?\b', '', text, flags=re.IGNORECASE)

    words_to_remove = [
        'will', 'won', 'would',
        'the', 'a', 'an',
        'there', 'this', 'that',
        'have', 'has', 'had',
        'be', 'been', 'being',
        'for', 'from', 'with',
        'about', 'against',
        'between', 'into', 'through', 'during',
        'before', 'after',
        'above', 'below',
        'over', 'under', 'again',
        'off', 'more',
        'as', 'is', 'are', 'was', 'were',
        'when', 'where', 'while',
        'how', 'what', 'which',
        'who', 'whom', 'whose',
        'why', 'whether', 'if',
        'since', 'until', 'unless',
    ]

    for word in words_to_remove:
        text = re.sub(r'\b' + word + r'\b', '', text, flags=re.IGNORECASE)

    text = text.replace('$', '').replace(',', '')
    text = re.sub(r'[^\w\s-]', ' ', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    text = text.strip('-')

    return text


def platform_link_html(platform_name, market_url=None):
    """Generate colored platform link HTML"""
    platform_colors = {
        'Polymarket': '#03a9f4',
        'Opinion': '#d29922',
        'Opinion.trade': '#d29922',
        'Predict': '#9c27b0',
        'Predict.fun': '#9c27b0',
        'Kalshi': '#e53935',
    }
    platform_urls = {
        'Polymarket': 'https://polymarket.com',
        'Opinion': 'https://opinion.trade',
        'Opinion.trade': 'https://opinion.trade',
        'Predict': 'https://predict.fun',
        'Predict.fun': 'https://predict.fun',
        'Kalshi': 'https://kalshi.com',
    }
    color = platform_colors.get(platform_name, '#888')
    url = market_url if market_url else platform_urls.get(platform_name, '#')
    return f"<a href='{url}' target='_blank' style='color:{color};font-weight:600;text-decoration:none'>{platform_name}</a>"


def fetch_polymarket_data(config):
    """Fetch Polymarket markets - use best_ask (卖一价) for accurate arbitrage pricing"""
    try:
        from src.polymarket_api import PolymarketClient
        poly_client = PolymarketClient(config)
        # Full-site paginated fetch — Polymarket has ~28k active markets
        markets = poly_client.get_markets(limit=POLYMARKET_FETCH_LIMIT, active_only=True)

        parsed = []
        for m in markets:
            try:
                condition_id = m.get('conditionId', m.get('condition_id', ''))
                if not condition_id:
                    continue

                # Use bestAsk/bestBid for actionable prices (what you'd actually pay)
                # bestAsk = cost to buy Yes, 1-bestBid = cost to buy No
                best_ask = m.get('bestAsk')
                best_bid = m.get('bestBid')

                if best_ask is not None and best_bid is not None:
                    try:
                        yes_price = float(best_ask)
                        no_price = round(1.0 - float(best_bid), 4)
                    except (ValueError, TypeError):
                        yes_price = None
                        no_price = None
                else:
                    yes_price = None
                    no_price = None

                # Fallback to outcomePrices if bestAsk/bestBid not available
                if yes_price is None or no_price is None or yes_price <= 0 or no_price <= 0:
                    outcome_str = m.get('outcomePrices', '[]')
                    if isinstance(outcome_str, str):
                        prices = json.loads(outcome_str)
                    else:
                        prices = outcome_str
                    if not prices or len(prices) < 2:
                        continue
                    yes_price = float(prices[0])
                    no_price = float(prices[1])

                if yes_price <= 0 or no_price <= 0:
                    continue

                question = m.get('question', '')
                events = m.get('events', [])
                event_title = events[0].get('title', '') if events else ''

                # Build match_title: include event context for cross-platform matching
                # Sub-questions like "Trump out as President" under event
                # "What will happen before GTA VI?" must include event context,
                # otherwise they falsely match "Trump out as President before 2027?"
                if event_title and event_title.lower().rstrip('?').strip() != question.lower().rstrip('?').strip():
                    match_title = f"{question} | {event_title}"
                else:
                    match_title = question

                # Use market-level slug (unique per question), NOT event slug
                market_slug = m.get('slug', '')
                if not market_slug:
                    market_slug = events[0].get('slug', '') if events else ''
                if not market_slug:
                    market_slug = condition_id

                parsed.append({
                    'id': condition_id,
                    'title': f"<a href='https://polymarket.com/event/{market_slug}' target='_blank' style='color:#03a9f4;font-weight:600'>{question[:80]}</a>",
                    'url': f"https://polymarket.com/event/{market_slug}",
                    'yes': round(yes_price, 4),
                    'no': round(no_price, 4),
                    'volume': float(m.get('volume24hr', 0) or 0),
                    'liquidity': float(m.get('liquidity', 0) or 0),
                    'platform': 'polymarket',
                    'end_date': m.get('endDate', ''),
                    'match_title': match_title,
                })
            except (ValueError, TypeError, KeyError):
                continue
            except Exception as e:
                logger.warning(f"Polymarket parse error: {e}")
                continue

        logger.info(f"Polymarket: {len(parsed)} markets (0 extra HTTP)")
        return 'active', parsed
    except ImportError as e:
        logger.error(f"Polymarket import error: {e}")
        return 'error', []
    except Exception as e:
        logger.error(f"Polymarket fetch error: {e}")
        return 'error', []


def fetch_opinion_data(config):
    """Fetch Opinion.trade markets - use best_ask (卖一价) for accurate pricing"""
    api_key = config.get('opinion', {}).get('api_key', '')
    if not api_key:
        logger.warning("Opinion: no API key")
        return 'no_key', []

    try:
        from src.opinion_api import OpinionAPIClient
        client = OpinionAPIClient(config)

        raw_markets = client.get_markets(status='activated', sort_by=5, limit=OPINION_MARKET_LIMIT)

        if not raw_markets:
            return 'error', []

        logger.info(f"Opinion: {len(raw_markets)} raw markets, fetching orderbook ask prices...")

        # Filter markets with both yes and no tokens
        valid_markets = []
        for m in raw_markets:
            if m.get('yesTokenId') and m.get('noTokenId'):
                valid_markets.append(m)
        logger.info(f"Opinion: {len(valid_markets)} markets have yes+no tokens")

        # Concurrent orderbook fetching — extract best_ask (卖一价)
        # Opinion has separate orderbooks for Yes and No tokens
        # We fetch BOTH in one pass to minimize total API calls
        token_asks = {}  # token_id -> best_ask price
        _op_call_count = [0]
        _op_lock = threading.Lock()

        def fetch_token_ask(token_id):
            """Fetch orderbook for a token and return its best_ask (卖一价)"""
            with _op_lock:
                _op_call_count[0] += 1
                # Rate limit: ~12 req/s (stay under 15 req/s limit)
                if _op_call_count[0] % 12 == 0:
                    time.sleep(1.0)
            ob = client.get_order_book(token_id)
            if ob is not None:
                return token_id, ob.yes_ask  # yes_ask field = ask price of queried token
            return token_id, None

        # Build list of all tokens to fetch (yes + no for top OPINION_DETAILED_FETCH,
        # yes-only for the rest)
        tokens_to_fetch = []
        token_to_market = {}  # token_id -> (market_index, 'yes'|'no')
        for idx, m in enumerate(valid_markets):
            yes_tok = m['yesTokenId']
            tokens_to_fetch.append(yes_tok)
            token_to_market[yes_tok] = (idx, 'yes')
            # Fetch No token orderbook for top markets
            if idx < OPINION_DETAILED_FETCH:
                no_tok = m['noTokenId']
                tokens_to_fetch.append(no_tok)
                token_to_market[no_tok] = (idx, 'no')

        logger.info(f"Opinion: fetching {len(tokens_to_fetch)} orderbooks concurrently ({len(valid_markets)} yes + {min(len(valid_markets), OPINION_DETAILED_FETCH)} no)")

        with ThreadPoolExecutor(max_workers=PRICE_FETCH_WORKERS, thread_name_prefix='op-ob') as ex:
            futures = {ex.submit(fetch_token_ask, t): t for t in tokens_to_fetch}
            for future in as_completed(futures, timeout=120):
                try:
                    token_id, ask_price = future.result(timeout=15)
                    if ask_price is not None:
                        token_asks[token_id] = ask_price
                except Exception:
                    pass

        yes_count = sum(1 for t in valid_markets if t['yesTokenId'] in token_asks)
        no_count = sum(1 for t in valid_markets[:OPINION_DETAILED_FETCH] if t['noTokenId'] in token_asks)
        logger.info(f"Opinion: got {yes_count} Yes asks, {no_count} No asks (best_ask)")

        # Build parsed list
        parsed = []
        for idx, m in enumerate(valid_markets):
            try:
                market_id = str(m.get('marketId', ''))
                title = m.get('marketTitle', '')
                yes_token = m['yesTokenId']
                no_token = m['noTokenId']

                yes_ask = token_asks.get(yes_token)
                if yes_ask is None:
                    continue

                # Use No token's ask if fetched, otherwise fallback to 1 - yes_ask
                no_ask = token_asks.get(no_token)
                if no_ask is None:
                    no_ask = round(1.0 - yes_ask, 4)

                if yes_ask <= 0 or yes_ask >= 1 or no_ask <= 0 or no_ask >= 1:
                    continue

                parsed.append({
                    'id': market_id,
                    'title': f"<a href='https://app.opinion.trade/detail?topicId={market_id}' target='_blank' style='color:#d29922;font-weight:600'>{title[:80]}</a>",
                    'url': f"https://app.opinion.trade/detail?topicId={market_id}",
                    'yes': round(yes_ask, 4),
                    'no': round(no_ask, 4),
                    'volume': float(m.get('volume24h', m.get('volume', 0)) or 0),
                    'liquidity': 0,
                    'platform': 'opinion',
                    'end_date': m.get('cutoff_at', ''),
                })
            except (ValueError, TypeError, KeyError):
                continue
            except Exception as e:
                logger.warning(f"Opinion parse error: {e}")
                continue

            if len(parsed) >= OPINION_PARSED_LIMIT:
                break

        logger.info(f"Opinion: {len(parsed)} markets (all using best_ask pricing)")
        return 'active', parsed
    except ImportError as e:
        logger.error(f"Opinion import error: {e}")
        return 'error', []
    except Exception as e:
        logger.error(f"Opinion fetch error: {e}")
        return 'error', []


def fetch_predict_data(config):
    """Fetch ALL Predict.fun open markets via cursor pagination + concurrent orderbook"""
    api_key = config.get('api', {}).get('api_key', '')
    base_url = config.get('api', {}).get('base_url', 'https://api.predict.fun')
    if not api_key:
        logger.warning("Predict: no API key configured (PREDICT_API_KEY env var)")
        return 'no_key', []

    logger.info(f"Predict: checking API at {base_url}/v1 (key: {api_key[:8]}...)")

    try:
        import requests as req
        from src.api_client import PredictAPIClient
        client = PredictAPIClient(config)

        # === Phase 1: Fetch ALL open markets via cursor pagination ===
        # Predict v1 API uses 'cursor' field (NOT 'after') for pagination
        # Total open markets: ~500+, page size max: 100
        session = req.Session()
        session.headers.update({'x-api-key': api_key, 'Content-Type': 'application/json'})

        all_raw = []
        seen_ids = set()
        cursor = None

        for page in range(10):  # max 10 pages = 1000 markets
            params = {'first': 100, 'status': 'OPEN', 'sort': 'VOLUME_24H_DESC'}
            if cursor:
                params['after'] = cursor
            resp = session.get(f"{base_url}/v1/markets", params=params, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"Predict page {page}: HTTP {resp.status_code}")
                break
            data = resp.json()
            batch = data.get('data', []) if isinstance(data, dict) else data
            if not batch:
                break
            new_count = 0
            for m in batch:
                mid = m.get('id', m.get('market_id', ''))
                if mid and mid not in seen_ids:
                    seen_ids.add(mid)
                    all_raw.append(m)
                    new_count += 1
            # v1 API returns cursor in 'cursor' field
            cursor = data.get('cursor') if isinstance(data, dict) else None
            logger.info(f"Predict [page {page}]: {len(batch)} fetched, {new_count} new, total={len(all_raw)}")
            if not cursor or len(batch) < 100:
                break

        if not all_raw:
            logger.warning("Predict: 0 raw markets returned from API")
            return 'error', []

        logger.info(f"Predict: {len(all_raw)} total open markets (full pagination)")

        # === Phase 2: Concurrent orderbook fetching for ALL markets ===
        orderbook_results = {}

        def fetch_orderbook(market_id):
            full_ob = client.get_full_orderbook(market_id)
            return market_id, full_ob

        # Fetch ALL market orderbooks concurrently
        markets_to_fetch = []
        for m in all_raw:
            mid = m.get('id', m.get('market_id', ''))
            if mid:
                markets_to_fetch.append(mid)

        logger.info(f"Predict: fetching {len(markets_to_fetch)} orderbooks concurrently...")

        with ThreadPoolExecutor(max_workers=PRICE_FETCH_WORKERS, thread_name_prefix='pred-ob') as ex:
            futures = {ex.submit(fetch_orderbook, mid): mid for mid in markets_to_fetch}
            for future in as_completed(futures, timeout=180):
                try:
                    mid, ob = future.result(timeout=15)
                    if ob is not None:
                        orderbook_results[mid] = ob
                except Exception:
                    pass

        logger.info(f"Predict: got {len(orderbook_results)} orderbooks")

        # === Phase 3: Build parsed list — filter extreme prices, sort by volume ===
        parsed = []
        extreme_count = 0
        for m in all_raw:
            try:
                market_id = m.get('id', m.get('market_id', ''))
                if not market_id:
                    continue

                ob = orderbook_results.get(market_id)
                if not ob:
                    continue

                yes_price = ob.get('yes_ask')
                no_price = ob.get('no_ask')
                if yes_price is None or no_price is None:
                    continue
                if yes_price <= 0 or no_price <= 0:
                    continue

                # Filter extreme prices: skip markets where outcome is near-certain
                # These markets (< 3% or > 97%) have no arbitrage value
                if yes_price < PREDICT_EXTREME_FILTER or yes_price > (1 - PREDICT_EXTREME_FILTER):
                    extreme_count += 1
                    continue

                question_text = (m.get('question') or m.get('title', ''))
                market_slug = slugify(question_text)
                parsed.append({
                    'id': market_id,
                    'title': f"<a href='https://predict.fun/market/{market_slug}' target='_blank' style='color:#9c27b0;font-weight:600'>{question_text[:80]}</a>",
                    'url': f"https://predict.fun/market/{market_slug}",
                    'yes': round(yes_price, 4),
                    'no': round(no_price, 4),
                    'volume': float(m.get('volume', 0) or 0),
                    'liquidity': float(m.get('liquidity', 0) or 0),
                    'platform': 'predict',
                    'end_date': '',
                })
            except Exception as e:
                logger.debug(f"Predict parse error: {e}")
                continue

        # Sort by volume descending (most active markets first)
        parsed.sort(key=lambda x: x['volume'], reverse=True)

        logger.info(f"Predict: {len(parsed)} markets with prices ({extreme_count} extreme filtered out)")
        return 'active', parsed
    except ImportError as e:
        logger.error(f"Predict import error: {e}")
        return 'error', []
    except Exception as e:
        logger.error(f"Predict fetch error: {e}")
        return 'error', []


def fetch_kalshi_data(config):
    """Fetch Kalshi markets — prices included in /markets response (no orderbook calls)"""
    try:
        from src.kalshi_api import KalshiClient
        client = KalshiClient(config)
        raw_markets = client.get_markets(status='open', limit=KALSHI_FETCH_LIMIT)
        logger.info(f"Kalshi: fetched {len(raw_markets)} raw markets")

        parsed = []
        for m in raw_markets:
            try:
                ticker = m.get('ticker', '')
                title = m.get('title', '') or m.get('subtitle', '') or ticker
                event_ticker = m.get('event_ticker', '')

                # Parse prices from dollar strings
                yes_ask_str = m.get('yes_ask_dollars', '0')
                no_ask_str = m.get('no_ask_dollars', '0')
                yes_ask = float(yes_ask_str) if yes_ask_str else 0
                no_ask = float(no_ask_str) if no_ask_str else 0

                if yes_ask <= 0 and no_ask <= 0:
                    continue

                # Fallback: derive missing price
                if yes_ask <= 0 and no_ask > 0:
                    yes_ask = 1.0 - float(m.get('no_bid_dollars', '0') or '0')
                if no_ask <= 0 and yes_ask > 0:
                    no_ask = 1.0 - float(m.get('yes_bid_dollars', '0') or '0')

                # Filter extreme prices
                if yes_ask < PREDICT_EXTREME_FILTER or yes_ask > (1 - PREDICT_EXTREME_FILTER):
                    continue

                url = f"https://kalshi.com/markets/{event_ticker}"
                volume = float(m.get('volume_24h', 0) or 0)
                close_time = m.get('close_time', '')

                parsed.append({
                    'id': ticker,
                    'title': f"<a href='{url}' target='_blank' style='color:#58a6ff;text-decoration:none'>{title[:80]}</a>",
                    'url': url,
                    'match_title': title,
                    'yes': round(yes_ask, 4),
                    'no': round(no_ask, 4),
                    'volume': volume,
                    'liquidity': float(m.get('liquidity_dollars', '0') or '0'),
                    'platform': 'kalshi',
                    'end_date': close_time,
                })
            except (ValueError, TypeError, KeyError):
                continue

        # Sort by volume
        parsed.sort(key=lambda x: x['volume'], reverse=True)
        logger.info(f"Kalshi: {len(parsed)} markets with valid prices")
        return 'active', parsed
    except ImportError as e:
        logger.error(f"Kalshi import error: {e}")
        return 'error', []
    except Exception as e:
        logger.error(f"Kalshi fetch error: {e}")
        return 'error', []


def update_price_history(arbitrage_list):
    """Track price history for arbitrage opportunities (last N data points per market)"""
    now_str = datetime.now().strftime('%H:%M:%S')
    history = _state.get('price_history', {})

    # Record current prices for active opportunities
    for opp in arbitrage_list:
        key = opp.get('market_key', '')
        if not key:
            continue
        if key not in history:
            history[key] = []
        history[key].append({
            'time': now_str,
            'arb': round(opp.get('arbitrage', 0), 2),
            'net': round(opp.get('net_profit', 0), 2),
        })
        # Keep only last N points
        if len(history[key]) > PRICE_HISTORY_MAX_POINTS:
            history[key] = history[key][-PRICE_HISTORY_MAX_POINTS:]

    # Prune markets no longer in active arbitrage
    active_keys = {opp.get('market_key', '') for opp in arbitrage_list}
    stale_keys = [k for k in history if k not in active_keys]
    for k in stale_keys:
        # Keep for a while in case it reappears
        if len(history[k]) > 0:
            history[k].append({'time': now_str, 'arb': 0, 'net': 0})
        if len(history[k]) > PRICE_HISTORY_MAX_POINTS:
            del history[k]

    _state['price_history'] = history


def find_cross_platform_arbitrage(markets_a, markets_b, platform_a_name, platform_b_name, threshold=2.0):
    """Find arbitrage between two platform market lists"""
    from src.market_matcher import MarketMatcher

    opportunities = []
    checked_pairs = 0
    skipped_end_date = 0

    markets_a_plain = []
    for m in markets_a:
        m_copy = m.copy()
        m_copy['title_plain'] = strip_html(m.get('title', ''))
        m_copy['title_with_html'] = m.get('title', '')
        # Use match_title (includes event context for Polymarket sub-questions)
        m_copy['match_title'] = m.get('match_title', '') or m_copy['title_plain']
        markets_a_plain.append(m_copy)

    markets_b_plain = []
    for m in markets_b:
        m_copy = m.copy()
        m_copy['title_plain'] = strip_html(m.get('title', ''))
        m_copy['title_with_html'] = m.get('title', '')
        m_copy['match_title'] = m.get('match_title', '') or m_copy['title_plain']
        markets_b_plain.append(m_copy)

    matcher = MarketMatcher({})
    matched_pairs = matcher.match_markets_cross_platform(
        markets_a_plain, markets_b_plain,
        title_field_a='match_title', title_field_b='match_title',
        id_field_a='id', id_field_b='id',
        platform_a=platform_a_name.lower(), platform_b=platform_b_name.lower(),
        min_similarity=0.50,
    )

    logger.info(f"[{platform_a_name} vs {platform_b_name}] Matched {len(matched_pairs)} pairs")

    for ma, mb, confidence in matched_pairs:
        checked_pairs += 1

        end_date_a = ma.get('end_date', '')
        end_date_b = mb.get('end_date', '')
        if end_date_a and end_date_b:
            try:
                if isinstance(end_date_a, str):
                    end_a = datetime.fromisoformat(end_date_a.replace('Z', '+00:00'))
                else:
                    end_a = end_date_a
                if isinstance(end_date_b, str):
                    end_b = datetime.fromisoformat(end_date_b.replace('Z', '+00:00'))
                else:
                    end_b = end_date_b

                time_diff = abs((end_a - end_b).days)
                if time_diff > 30:
                    skipped_end_date += 1
                    continue
            except Exception:
                pass

        combined1 = ma['yes'] + mb['no']
        arb1 = (1.0 - combined1) * 100

        combined2 = mb['yes'] + ma['no']
        arb2 = (1.0 - combined2) * 100

        market_key_base = f"{platform_a_name}-{platform_b_name}-{ma.get('id','')}-{mb.get('id','')}"
        now_str = datetime.now().strftime('%H:%M:%S')

        fee_a = PLATFORM_FEES.get(platform_a_name.lower(), 0.02)
        fee_b = PLATFORM_FEES.get(platform_b_name.lower(), 0.02)

        if arb1 >= threshold:
            fee_cost1 = (ma['yes'] * fee_a + mb['no'] * fee_b) * 100
            opportunities.append({
                'market': strip_html(ma['title_with_html']),
                'platform_a': platform_link_html(platform_a_name, ma.get('url')),
                'platform_b': platform_link_html(platform_b_name, mb.get('url')),
                'direction': f"{platform_a_name} Buy Yes + {platform_b_name} Buy No",
                'a_yes': round(ma['yes'] * 100, 2),
                'a_no': round(ma['no'] * 100, 2),
                'b_yes': round(mb['yes'] * 100, 2),
                'b_no': round(mb['no'] * 100, 2),
                'combined': round(combined1 * 100, 2),
                'arbitrage': round(arb1, 2),
                'net_profit': round(arb1 - fee_cost1, 2),
                'confidence': round(confidence, 2),
                'timestamp': now_str,
                'market_key': f"{market_key_base}-yes1_no2",
                '_created_at': time.time(),
                'arb_type': 'cross_platform',
            })

        if arb2 >= threshold:
            fee_cost2 = (mb['yes'] * fee_b + ma['no'] * fee_a) * 100
            opportunities.append({
                'market': strip_html(mb['title_with_html']),
                'platform_a': platform_link_html(platform_b_name, mb.get('url')),
                'platform_b': platform_link_html(platform_a_name, ma.get('url')),
                'direction': f"{platform_b_name} Buy Yes + {platform_a_name} Buy No",
                'a_yes': round(mb['yes'] * 100, 2),
                'a_no': round(mb['no'] * 100, 2),
                'b_yes': round(ma['yes'] * 100, 2),
                'b_no': round(ma['no'] * 100, 2),
                'combined': round(combined2 * 100, 2),
                'arbitrage': round(arb2, 2),
                'net_profit': round(arb2 - fee_cost2, 2),
                'confidence': round(confidence, 2),
                'timestamp': now_str,
                'market_key': f"{market_key_base}-yes2_no1",
                '_created_at': time.time(),
                'arb_type': 'cross_platform',
            })

    opportunities.sort(key=lambda x: x['arbitrage'], reverse=True)

    logger.info(
        f"[{platform_a_name} vs {platform_b_name}] Checked: {checked_pairs}, "
        f"Skipped(end_date): {skipped_end_date}, "
        f"Found: {len(opportunities)}"
    )

    return opportunities


def find_same_platform_arbitrage(markets, platform_name, threshold=0.5):
    """Detect same-platform arbitrage: Yes_ask + No_ask < $1.00
    If you can buy both Yes and No for less than $1, guaranteed profit on resolution.
    """
    opportunities = []
    fee_rate = PLATFORM_FEES.get(platform_name.lower(), 0.02)
    now_str = datetime.now().strftime('%H:%M:%S')

    for m in markets:
        try:
            yes_ask = m.get('yes', 0)
            no_ask = m.get('no', 0)
            if not yes_ask or not no_ask or yes_ask <= 0 or no_ask <= 0:
                continue

            total_cost = yes_ask + no_ask  # cost to buy both outcomes
            if total_cost >= 1.0:
                continue  # no arbitrage

            gross_pct = (1.0 - total_cost) * 100
            # Net profit after fees on both legs
            fee_cost = (yes_ask * fee_rate + no_ask * fee_rate) * 100
            net_pct = gross_pct - fee_cost

            if gross_pct < threshold:
                continue

            title_plain = strip_html(m.get('title', ''))
            market_key = f"SAME-{platform_name}-{m.get('id', '')}"

            opportunities.append({
                'market': title_plain,
                'platform_a': platform_link_html(platform_name, m.get('url')),
                'platform_b': platform_link_html(platform_name, m.get('url')),
                'direction': f"{platform_name} Buy Yes + Buy No (same platform)",
                'a_yes': round(yes_ask * 100, 2),
                'a_no': round(no_ask * 100, 2),
                'b_yes': round(yes_ask * 100, 2),
                'b_no': round(no_ask * 100, 2),
                'combined': round(total_cost * 100, 2),
                'arbitrage': round(gross_pct, 2),
                'net_profit': round(net_pct, 2),
                'confidence': 1.0,  # same platform = 100% confidence
                'timestamp': now_str,
                'market_key': market_key,
                '_created_at': time.time(),
                'arb_type': 'same_platform',
            })
        except Exception:
            continue

    opportunities.sort(key=lambda x: x['arbitrage'], reverse=True)
    if opportunities:
        logger.info(f"[{platform_name} SAME] Found {len(opportunities)} same-platform arbitrage")
    return opportunities


def _emit_state(event='state_update'):
    """Push current state to all connected WebSocket clients"""
    if _ws_clients > 0:
        try:
            with _lock:
                state_copy = json.loads(json.dumps(_state, default=str))
            socketio.emit(event, state_copy, namespace='/')
        except Exception as e:
            logger.debug(f"WebSocket emit error: {e}")


def _emit_platform_update(platform, status, count):
    """Push a single platform update to WebSocket clients"""
    if _ws_clients > 0:
        try:
            socketio.emit('platform_update', {
                'platform': platform,
                'status': status,
                'count': count,
                'timestamp': datetime.now().strftime('%H:%M:%S'),
            }, namespace='/')
        except Exception as e:
            logger.debug(f"WebSocket platform emit error: {e}")


def _on_ws_price_update(platform: str, market_id: str, yes_ask: float, no_ask: float):
    """Callback from WebSocket feeds when a price changes in real-time.

    Updates the in-memory price cache and pushes incremental updates to frontend.
    """
    global _ws_update_count
    _ws_update_count += 1

    with _lock:
        if platform not in _ws_prices:
            _ws_prices[platform] = {}
        _ws_prices[platform][market_id] = {"yes_ask": yes_ask, "no_ask": no_ask}

    # Push real-time price update to frontend via Flask-SocketIO
    if _ws_clients > 0:
        try:
            socketio.emit('ws_price_update', {
                'platform': platform,
                'market_id': market_id,
                'yes_ask': round(yes_ask, 4),
                'no_ask': round(no_ask, 4),
                'timestamp': datetime.now().strftime('%H:%M:%S'),
            }, namespace='/')
        except Exception:
            pass


def background_scanner():
    """Background thread: scan all platforms with concurrent fetch + WebSocket push"""
    global _state, _realtime_feed
    config = load_config()
    threshold = float(config.get('opinion_poly', {}).get('min_arbitrage_threshold', 2.0))
    scan_interval = int(config.get('arbitrage', {}).get('scan_interval', 60))

    logger.info(f"Scanner started: threshold={threshold}%, interval={scan_interval}s")
    logger.info(f"Limits: poly={POLYMARKET_FETCH_LIMIT}, "
                f"opinion_detailed={OPINION_DETAILED_FETCH}, "
                f"predict=ALL(paginated), workers={PRICE_FETCH_WORKERS}")

    # Start real-time WebSocket price feeds
    try:
        from src.ws_price_feed import RealtimePriceFeed
        _realtime_feed = RealtimePriceFeed(on_price_update=_on_ws_price_update)
        _realtime_feed.start()
        logger.info("Real-time WebSocket price feeds started")
    except Exception as e:
        logger.warning(f"WebSocket feeds failed to start (falling back to polling only): {e}")
        _realtime_feed = None

    while True:
        # Scan guard: skip if previous scan is still running
        if _scanning.is_set():
            logger.warning("Previous scan still running, skipping this cycle")
            time.sleep(scan_interval)
            continue

        _scanning.set()
        scan_start = time.time()

        try:
            now = time.time()

            # === CONCURRENT FETCHING: all 4 platforms in parallel ===
            poly_status, poly_markets = 'unknown', []
            opinion_status, opinion_markets = 'unknown', []
            predict_status, predict_markets = 'unknown', []
            kalshi_status, kalshi_markets = 'unknown', []

            with ThreadPoolExecutor(max_workers=4, thread_name_prefix='fetch') as executor:
                futures = {
                    executor.submit(fetch_polymarket_data, config): 'polymarket',
                    executor.submit(fetch_opinion_data, config): 'opinion',
                    executor.submit(fetch_predict_data, config): 'predict',
                    executor.submit(fetch_kalshi_data, config): 'kalshi',
                }

                for future in as_completed(futures, timeout=120):
                    platform = futures[future]
                    try:
                        status, markets = future.result(timeout=120)
                        if platform == 'polymarket':
                            poly_status, poly_markets = status, markets
                        elif platform == 'opinion':
                            opinion_status, opinion_markets = status, markets
                        elif platform == 'predict':
                            predict_status, predict_markets = status, markets
                        elif platform == 'kalshi':
                            kalshi_status, kalshi_markets = status, markets

                        # Update state immediately for this platform
                        with _lock:
                            _state['platforms'][platform] = {
                                'status': status,
                                'markets': markets[:MAX_MARKETS_DISPLAY],
                                'count': len(markets),
                                'last_update': now,
                            }
                        logger.info(f"[{platform}] {len(markets)} markets, status={status}")

                        # Push per-platform update via WebSocket (real-time)
                        _emit_platform_update(platform, status, len(markets))

                    except Exception as e:
                        logger.error(f"[{platform}] fetch failed: {e}")
                        with _lock:
                            _state['platforms'][platform] = {
                                'status': 'error',
                                'markets': [],
                                'count': 0,
                                'last_update': now,
                            }
                        _emit_platform_update(platform, 'error', 0)

            # === Same-platform arbitrage (Yes+No < $1.00) ===
            all_arb = []
            platform_market_pairs = [
                ('Polymarket', poly_markets), ('Opinion', opinion_markets),
                ('Predict', predict_markets), ('Kalshi', kalshi_markets),
            ]
            for pname, pmarkets in platform_market_pairs:
                if pmarkets:
                    same_arb = find_same_platform_arbitrage(pmarkets, pname, threshold=0.5)
                    all_arb.extend(same_arb)

            # === Cross-platform arbitrage (all pairs of 4 platforms) ===
            cross_pairs_checked = 0
            cross_platform_combos = [
                (poly_markets, opinion_markets, 'Polymarket', 'Opinion'),
                (poly_markets, predict_markets, 'Polymarket', 'Predict'),
                (poly_markets, kalshi_markets, 'Polymarket', 'Kalshi'),
                (opinion_markets, predict_markets, 'Opinion', 'Predict'),
                (opinion_markets, kalshi_markets, 'Opinion', 'Kalshi'),
                (predict_markets, kalshi_markets, 'Predict', 'Kalshi'),
            ]
            for markets_a, markets_b, name_a, name_b in cross_platform_combos:
                if markets_a and markets_b:
                    arb = find_cross_platform_arbitrage(
                        markets_a, markets_b, name_a, name_b, threshold)
                    all_arb.extend(arb)
                    cross_pairs_checked += 1

            all_arb.sort(key=lambda x: x['arbitrage'], reverse=True)

            with _lock:
                # Merge with existing, but expire stale entries
                existing_arb = _state.get('arbitrage', [])
                new_arb_map = {opp['market_key']: opp for opp in all_arb if opp.get('market_key')}
                old_arb_map = {opp['market_key']: opp for opp in existing_arb if opp.get('market_key')}

                # Keep old opportunities only if they haven't expired
                expiry_cutoff = time.time() - (ARBITRAGE_EXPIRY_MINUTES * 60)
                for key, old_opp in old_arb_map.items():
                    if key not in new_arb_map:
                        created_at = old_opp.get('_created_at', 0)
                        if created_at > expiry_cutoff:
                            new_arb_map[key] = old_opp

                # For opportunities in both, keep new if price changed significantly
                for key in list(new_arb_map.keys()):
                    new_opp = new_arb_map[key]
                    if key in old_arb_map:
                        old_opp = old_arb_map[key]
                        price_change = abs(new_opp['arbitrage'] - old_opp['arbitrage'])
                        if price_change < 0.5:
                            old_opp['timestamp'] = datetime.now().strftime('%H:%M:%S')
                            new_arb_map[key] = old_opp

                # Sort and limit
                sorted_arb = sorted(new_arb_map.values(), key=lambda x: x['arbitrage'], reverse=True)
                _state['arbitrage'] = sorted_arb[:MAX_ARBITRAGE_DISPLAY]
                _state['scan_count'] += 1
                _state['last_scan'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                _state['threshold'] = threshold
                _state['error'] = None

                # Scan statistics for UI
                scan_duration = time.time() - scan_start
                same_count = sum(1 for a in all_arb if a.get('arb_type') == 'same_platform')
                cross_count = sum(1 for a in all_arb if a.get('arb_type') == 'cross_platform')
                profitable = sum(1 for a in all_arb if a.get('net_profit', 0) > 0)
                _state['scan_stats'] = {
                    'duration': round(scan_duration, 1),
                    'total_markets': len(poly_markets) + len(opinion_markets) + len(predict_markets) + len(kalshi_markets),
                    'poly_count': len(poly_markets),
                    'opinion_count': len(opinion_markets),
                    'predict_count': len(predict_markets),
                    'kalshi_count': len(kalshi_markets),
                    'cross_pairs_checked': cross_pairs_checked,
                    'same_platform_arb': same_count,
                    'cross_platform_arb': cross_count,
                    'profitable_after_fees': profitable,
                    'total_arb': len(all_arb),
                    'ws_updates': _ws_update_count,
                }

                # Update price history for active arb opportunities
                update_price_history(sorted_arb[:MAX_ARBITRAGE_DISPLAY])

            logger.info(
                f"Scan #{_state['scan_count']} ({scan_duration:.1f}s): "
                f"Poly={len(poly_markets)} Opinion={len(opinion_markets)} "
                f"Predict={len(predict_markets)} Kalshi={len(kalshi_markets)} "
                f"Arb={len(all_arb)}(same={same_count},cross={cross_count},net+={profitable}) "
                f"WS={_ws_clients}"
            )

            # Push full state update to all WebSocket clients
            _emit_state()

            # Update real-time WebSocket subscriptions for active arb markets
            if _realtime_feed:
                try:
                    _realtime_feed.update_arb_markets(
                        all_arb,
                        {
                            'polymarket': poly_markets,
                            'kalshi': kalshi_markets,
                        }
                    )
                    ws_stats = _realtime_feed.stats
                    with _lock:
                        _state['ws_feed'] = {
                            'poly_subscribed': ws_stats.get('poly_subscribed', 0),
                            'kalshi_subscribed': ws_stats.get('kalshi_subscribed', 0),
                            'poly_connected': ws_stats.get('poly_connected', False),
                            'kalshi_connected': ws_stats.get('kalshi_connected', False),
                            'ws_updates': _ws_update_count,
                        }
                except Exception as e:
                    logger.debug(f"WS subscription update error: {e}")

        except Exception as e:
            logger.error(f"Scanner error: {e}")
            logger.error(traceback.format_exc())
            with _lock:
                _state['error'] = str(e)
            _emit_state()
        finally:
            _scanning.clear()

        time.sleep(scan_interval)


# ============================================================
# WebSocket event handlers
# ============================================================

@socketio.on('connect')
def handle_connect():
    global _ws_clients
    _ws_clients += 1
    logger.info(f"WebSocket client connected (total: {_ws_clients})")
    # Send current state immediately on connect
    with _lock:
        state_copy = json.loads(json.dumps(_state, default=str))
    emit('state_update', state_copy)


@socketio.on('disconnect')
def handle_disconnect():
    global _ws_clients
    _ws_clients = max(0, _ws_clients - 1)
    logger.info(f"WebSocket client disconnected (total: {_ws_clients})")


@socketio.on('request_state')
def handle_request_state():
    """Client can explicitly request current state"""
    with _lock:
        state_copy = json.loads(json.dumps(_state, default=str))
    emit('state_update', state_copy)


# ============================================================
# HTTP routes (kept as fallback)
# ============================================================

@app.route('/')
def index():
    try:
        return render_template('index.html')
    except Exception as e:
        logger.error(f"Template error: {e}")
        return f"<h1>Dashboard Error</h1><p>{e}</p><pre>{traceback.format_exc()}</pre>"


@app.route('/api/state')
def api_state():
    """HTTP fallback for clients without WebSocket"""
    with _lock:
        return jsonify(_state)


@app.route('/health')
def health():
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'ws_clients': _ws_clients,
        'scan_count': _state.get('scan_count', 0),
    })


def main():
    port = int(os.getenv('PORT', 5000))

    logger.info("=" * 60)
    logger.info("  Prediction Market Arbitrage Dashboard v3.2")
    logger.info("  WebSocket + concurrent fetch + scan guard")
    logger.info(f"  Binding to 0.0.0.0:{port}")
    logger.info("=" * 60)
    logger.info(f"Templates folder: {app.template_folder}")

    # Start background scanner AFTER Flask binds (delayed start)
    def start_scanner_delayed():
        """Wait a few seconds for Flask to fully bind, then start scanning"""
        time.sleep(3)
        logger.info("Background scanner starting...")
        background_scanner()

    scanner = threading.Thread(target=start_scanner_delayed, daemon=True)
    scanner.start()

    # Start Flask-SocketIO - this must happen IMMEDIATELY so Railway
    # can detect the service is listening on the port
    logger.info(f">>> Flask-SocketIO starting on http://0.0.0.0:{port}")
    socketio.run(app, host='0.0.0.0', port=port, debug=False,
                 allow_unsafe_werkzeug=True)


if __name__ == '__main__':
    main()
