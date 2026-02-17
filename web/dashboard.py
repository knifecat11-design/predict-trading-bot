"""
Cross-platform prediction market arbitrage dashboard
Platforms: Polymarket, Opinion.trade, Predict.fun

v3.1: WebSocket push + concurrent fetching + scan guard + memory limits
"""

import os
import sys
import json
import time
import logging
import threading
import traceback
from datetime import datetime, timedelta
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
OPINION_DETAILED_FETCH = 30       # Individual No price fetches (was 80 -> reduced to 30)
PREDICT_DETAILED_FETCH = 25       # Individual orderbook fetches (was 80 -> reduced to 25)
POLYMARKET_LIMIT_PER_TAG = 100    # Markets per tag (was 200 -> reduced to 100)
OPINION_MARKET_LIMIT = 200        # Total opinion markets to process (was 500 -> reduced)
OPINION_PARSED_LIMIT = 150        # Max parsed opinion markets (was 300 -> reduced)
MIN_SCAN_INTERVAL = 45            # Minimum seconds between scans (prevents overload)

# Global state
_state = {
    'platforms': {
        'polymarket': {'status': 'unknown', 'markets': [], 'last_update': 0, 'count': 0},
        'opinion': {'status': 'unknown', 'markets': [], 'last_update': 0, 'count': 0},
        'predict': {'status': 'unknown', 'markets': [], 'last_update': 0, 'count': 0},
    },
    'arbitrage': [],
    'scan_count': 0,
    'started_at': datetime.now().isoformat(),
    'threshold': 2.0,
    'last_scan': '-',
    'error': None,
}
_lock = threading.Lock()
_scanning = threading.Event()  # Scan guard: prevents overlapping scans


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
    }
    platform_urls = {
        'Polymarket': 'https://polymarket.com',
        'Opinion': 'https://opinion.trade',
        'Opinion.trade': 'https://opinion.trade',
        'Predict': 'https://predict.fun',
        'Predict.fun': 'https://predict.fun',
    }
    color = platform_colors.get(platform_name, '#888')
    url = market_url if market_url else platform_urls.get(platform_name, '#')
    return f"<a href='{url}' target='_blank' style='color:{color};font-weight:600;text-decoration:none'>{platform_name}</a>"


def fetch_polymarket_data(config):
    """Fetch Polymarket markets - optimized: reduced limit_per_tag"""
    try:
        from src.polymarket_api import PolymarketClient
        poly_client = PolymarketClient(config)
        # Full-site paginated fetch (more efficient than tag-by-tag with dedup loss)
        markets = poly_client.get_markets(limit=3000, active_only=True)

        parsed = []
        for m in markets:
            try:
                condition_id = m.get('conditionId', m.get('condition_id', ''))
                if not condition_id:
                    continue

                best_bid = m.get('bestBid')
                best_ask = m.get('bestAsk')

                if best_bid is not None and best_ask is not None:
                    yes_price = float(best_ask)
                    no_price = 1.0 - float(best_bid)
                else:
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

                events = m.get('events', [])
                event_slug = events[0].get('slug', '') if events else ''
                if not event_slug:
                    event_slug = condition_id

                parsed.append({
                    'id': condition_id,
                    'title': f"<a href='https://polymarket.com/event/{event_slug}' target='_blank' style='color:#03a9f4;font-weight:600'>{m.get('question', '')[:80]}</a>",
                    'url': f"https://polymarket.com/event/{event_slug}",
                    'yes': round(yes_price, 4),
                    'no': round(no_price, 4),
                    'volume': float(m.get('volume24hr', 0) or 0),
                    'liquidity': float(m.get('liquidity', 0) or 0),
                    'platform': 'polymarket',
                    'end_date': m.get('endDate', ''),
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
    """Fetch Opinion.trade markets - optimized: reduced individual price fetches"""
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

        logger.info(f"Opinion: {len(raw_markets)} raw markets, parsing prices...")

        parsed = []

        for idx, m in enumerate(raw_markets):
            try:
                market_id = str(m.get('marketId', ''))
                title = m.get('marketTitle', '')
                yes_token = m.get('yesTokenId', '')
                no_token = m.get('noTokenId', '')

                if not yes_token:
                    continue

                yes_price = client.get_token_price(yes_token)
                if yes_price is None:
                    continue

                # Only fetch individual No price for top markets, use fallback for rest
                if idx < OPINION_DETAILED_FETCH and no_token:
                    no_price = client.get_token_price(no_token)
                    if no_price is None:
                        no_price = round(1.0 - yes_price, 4)
                    # Rate limit: small delay every 10 requests to respect 15 req/s
                    if idx > 0 and idx % 10 == 0:
                        time.sleep(0.2)
                elif no_token:
                    no_price = round(1.0 - yes_price, 4)
                else:
                    no_price = None

                if no_price is None:
                    continue
                if yes_price <= 0 or yes_price >= 1 or no_price <= 0 or no_price >= 1:
                    continue

                parsed.append({
                    'id': market_id,
                    'title': f"<a href='https://app.opinion.trade/detail?topicId={market_id}' target='_blank' style='color:#d29922;font-weight:600'>{title[:80]}</a>",
                    'url': f"https://app.opinion.trade/detail?topicId={market_id}",
                    'yes': round(yes_price, 4),
                    'no': round(no_price, 4),
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

        logger.info(f"Opinion: {len(parsed)} markets (detailed: {OPINION_DETAILED_FETCH}, fallback: rest)")
        return 'active', parsed
    except ImportError as e:
        logger.error(f"Opinion import error: {e}")
        return 'error', []
    except Exception as e:
        logger.error(f"Opinion fetch error: {e}")
        return 'error', []


def fetch_predict_data(config):
    """Fetch Predict.fun markets - optimized: reduced orderbook fetches"""
    api_key = config.get('api', {}).get('api_key', '')
    base_url = config.get('api', {}).get('base_url', 'https://api.predict.fun')
    if not api_key:
        logger.warning("Predict: no API key configured (PREDICT_API_KEY env var)")
        return 'no_key', []

    logger.info(f"Predict: checking API at {base_url}/v1 (key: {api_key[:8]}...)")

    try:
        from src.api_client import PredictAPIClient
        client = PredictAPIClient(config)

        all_raw = []
        for sort_by in ['popular', 'newest']:
            try:
                batch = client.get_markets(status='open', sort=sort_by, limit=100)
                logger.info(f"Predict [{sort_by}]: got {len(batch)} markets")
                for m in batch:
                    mid = m.get('id', m.get('market_id', ''))
                    if mid and mid not in {x.get('id', x.get('market_id', '')) for x in all_raw}:
                        all_raw.append(m)
            except Exception as e:
                logger.error(f"Predict [{sort_by}] fetch failed: {e}")

        if not all_raw:
            logger.warning("Predict: 0 raw markets returned from API")
            return 'error', []

        logger.info(f"Predict: {len(all_raw)} raw markets")

        parsed = []

        # Debug: log first market's raw data to understand v1 API format
        if all_raw:
            first = all_raw[0]
            logger.info(f"Predict first market keys: {list(first.keys())[:15]}")
            logger.info(f"Predict first market: id={first.get('id')}, title={first.get('title', '')[:50]}, question={first.get('question', '')[:50]}")

        for idx, m in enumerate(all_raw):
            try:
                market_id = m.get('id', m.get('market_id', ''))
                if not market_id:
                    if idx < 3:
                        logger.warning(f"Predict market #{idx}: no id field, keys={list(m.keys())[:10]}")
                    continue

                question_text = (m.get('question') or m.get('title', ''))

                if idx < PREDICT_DETAILED_FETCH:
                    full_ob = client.get_full_orderbook(market_id)
                    if full_ob is None:
                        if idx < 3:
                            logger.warning(f"Predict market #{idx} (id={market_id}): orderbook returned None")
                        continue
                    yes_price = full_ob['yes_ask']
                    no_price = full_ob['no_ask']
                else:
                    # Fast mode: single orderbook call, derive No price
                    try:
                        yes_ob = client._get_orderbook(market_id, outcome_id=1)
                        if yes_ob is None:
                            continue
                        yes_price = yes_ob.get('yes_ask') or yes_ob.get('best_ask')
                        if yes_price is None:
                            continue
                        no_price = round(1.0 - yes_price, 4)
                    except Exception:
                        continue

                if yes_price is None or no_price is None:
                    continue
                if yes_price <= 0 or no_price <= 0:
                    continue

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

        logger.info(f"Predict: {len(parsed)} markets (detailed: {PREDICT_DETAILED_FETCH})")
        return 'active', parsed
    except ImportError as e:
        logger.error(f"Predict import error: {e}")
        return 'error', []
    except Exception as e:
        logger.error(f"Predict fetch error: {e}")
        return 'error', []


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
        markets_a_plain.append(m_copy)

    markets_b_plain = []
    for m in markets_b:
        m_copy = m.copy()
        m_copy['title_plain'] = strip_html(m.get('title', ''))
        m_copy['title_with_html'] = m.get('title', '')
        markets_b_plain.append(m_copy)

    matcher = MarketMatcher({})
    matched_pairs = matcher.match_markets_cross_platform(
        markets_a_plain, markets_b_plain,
        title_field_a='title_plain', title_field_b='title_plain',
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

        if arb1 >= threshold:
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
                'confidence': round(confidence, 2),
                'timestamp': now_str,
                'market_key': f"{market_key_base}-yes1_no2",
                '_created_at': time.time(),
            })

        if arb2 >= threshold:
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
                'confidence': round(confidence, 2),
                'timestamp': now_str,
                'market_key': f"{market_key_base}-yes2_no1",
                '_created_at': time.time(),
            })

    opportunities.sort(key=lambda x: x['arbitrage'], reverse=True)

    logger.info(
        f"[{platform_a_name} vs {platform_b_name}] Checked: {checked_pairs}, "
        f"Skipped(end_date): {skipped_end_date}, "
        f"Found: {len(opportunities)}"
    )

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


def background_scanner():
    """Background thread: scan all platforms with concurrent fetch + WebSocket push"""
    global _state
    config = load_config()
    threshold = float(config.get('opinion_poly', {}).get('min_arbitrage_threshold', 2.0))
    scan_interval = int(config.get('arbitrage', {}).get('scan_interval', 60))

    logger.info(f"Scanner started: threshold={threshold}%, interval={scan_interval}s")
    logger.info(f"Limits: poly_per_tag={POLYMARKET_LIMIT_PER_TAG}, "
                f"opinion_detailed={OPINION_DETAILED_FETCH}, "
                f"predict_detailed={PREDICT_DETAILED_FETCH}")

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

            # === CONCURRENT FETCHING: all 3 platforms in parallel ===
            poly_status, poly_markets = 'unknown', []
            opinion_status, opinion_markets = 'unknown', []
            predict_status, predict_markets = 'unknown', []

            with ThreadPoolExecutor(max_workers=3, thread_name_prefix='fetch') as executor:
                futures = {
                    executor.submit(fetch_polymarket_data, config): 'polymarket',
                    executor.submit(fetch_opinion_data, config): 'opinion',
                    executor.submit(fetch_predict_data, config): 'predict',
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

            # Find arbitrage across all pairs
            all_arb = []

            if poly_markets and opinion_markets:
                arb = find_cross_platform_arbitrage(
                    poly_markets, opinion_markets, 'Polymarket', 'Opinion', threshold)
                all_arb.extend(arb)

            if poly_markets and predict_markets:
                arb = find_cross_platform_arbitrage(
                    poly_markets, predict_markets, 'Polymarket', 'Predict', threshold)
                all_arb.extend(arb)

            if opinion_markets and predict_markets:
                arb = find_cross_platform_arbitrage(
                    opinion_markets, predict_markets, 'Opinion', 'Predict', threshold)
                all_arb.extend(arb)

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

            scan_duration = time.time() - scan_start
            logger.info(
                f"Scan #{_state['scan_count']} ({scan_duration:.1f}s): "
                f"Poly={len(poly_markets)} Opinion={len(opinion_markets)} "
                f"Predict={len(predict_markets)} Arb={len(all_arb)} "
                f"WS_clients={_ws_clients}"
            )

            # Push full state update to all WebSocket clients
            _emit_state()

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
