"""
Cross-platform prediction market arbitrage dashboard
Platforms: Polymarket, Opinion.trade, Predict.fun, Probable.markets, Kalshi

v3.4: + Improved market matching for person names (Eric vs Donald Trump)
"""

import os
import sys
import json
import time
import logging
import threading
import traceback
from datetime import datetime, timedelta, timezone
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

_TZ_CST = timezone(timedelta(hours=8))   # UTC+8 显示时间

# ============================================================
# Configuration constants - tune these to control resource usage
# ============================================================
MAX_MARKETS_DISPLAY = 15          # Max markets to store in state per platform (for UI)
MAX_ARBITRAGE_DISPLAY = 30        # Max arbitrage opportunities to keep
ARBITRAGE_EXPIRY_MINUTES = 10     # Remove stale arbitrage after N minutes
OPINION_DETAILED_FETCH = 100      # Individual No price fetches (concurrent)
POLYMARKET_FETCH_LIMIT = 5000     # Polymarket markets to fetch (total active ~28k)
OPINION_MARKET_LIMIT = 1000       # Total opinion markets to fetch (high cap, API returns ~150)
OPINION_PARSED_LIMIT = 800        # Max parsed opinion markets
PREDICT_EXTREME_FILTER = 0.02     # Filter markets with Yes < 2% or > 98%
PRICE_FETCH_WORKERS = 10          # Concurrent threads for price/orderbook fetching (default)
OPINION_FETCH_WORKERS = 12        # Concurrent threads for Opinion orderbook fetching
PREDICT_ORDERBOOK_WORKERS = 15    # Concurrent threads for Predict orderbook fetching
PREDICT_FETCH_MAX_PAGES = 50      # Max cursor pagination pages for Predict (20→50, up to 5000 markets)
MIN_SCAN_INTERVAL = 60            # Minimum seconds between scans (prevents overload)
KALSHI_FETCH_LIMIT = 5000         # Kalshi markets to fetch (all open markets)
PROBABLE_FETCH_LIMIT = 5000       # Probable events to fetch (was 1000)
PRICE_HISTORY_MAX_POINTS = 30     # Max price history data points per market
# Multi-outcome arb: minimum total cost (sum of all Yes-ask prices) to be considered valid.
# A genuine MECE event (election, sports champion) has outcomes summing close to $1.
# Non-exhaustive markets (e.g. FDV buckets missing a "<$1B" tier) sum to 10–30c and
# must be excluded — if no outcome covers the actual result, all positions expire worthless.
MULTI_OUTCOME_MIN_TOTAL_COST = 0.50   # Require sum ≥ 50c to pass MECE sanity check

# Probable Markets order book API: https://api.probable.markets/public/api/v1
# Enable Probable Markets arbitrage monitoring
PROBABLE_ARBITRAGE_ENABLED = True

# Logical Spread Arbitrage configuration
LSA_ENABLED = True                    # Enable logical spread arbitrage detection
LSA_USE_ORDERBOOK = False             # Use orderbook for executable cost (experimental)
LSA_TARGET_POSITION = 100.0           # Target position size (shares) for orderbook calculation
LSA_MAX_SLIPPAGE = 5.0                # Maximum acceptable slippage (%)

# Platform fee rates (used for net profit calculation)
PLATFORM_FEES = {
    'polymarket': 0.02,    # 2% taker fee
    'opinion': 0.02,       # 2% estimated
    'predict': 0.02,       # feeRateBps: 200 = 2%
    'kalshi': 0.02,        # ~2% effective (Kalshi: 7% of profit ≈ 2% of trade value)
    'probable': 0.00,      # 0% (Probable Markets claims zero fees)
    'pro': 0.00,           # Alias for Probable Markets
}

# Global state
_state = {
    'platforms': {
        'polymarket': {'status': 'unknown', 'markets': [], 'last_update': 0, 'count': 0},
        'opinion': {'status': 'unknown', 'markets': [], 'last_update': 0, 'count': 0},
        'predict': {'status': 'unknown', 'markets': [], 'last_update': 0, 'count': 0},
        'kalshi': {'status': 'unknown', 'markets': [], 'last_update': 0, 'count': 0},
        'probable': {'status': 'unknown', 'markets': [], 'last_update': 0, 'count': 0},
    },
    'arbitrage': [],
    'logical_spread_arb': [],  # Logical Spread Arbitrage: P(hard) >= P(easy) on same platform
    'multi_outcome_arb': [],   # Multi-result arbitrage: sum of Yes prices < $1 on same platform
    'scan_count': 0,
    'started_at': datetime.now().isoformat(),
    'threshold': 2.0,
    'last_scan': '-',
    'error': None,
    'price_history': {},  # market_key → [{timestamp, arbitrage, net_profit}, ...]
}

# Cache for raw Polymarket markets (cross-platform matching)
_poly_raw_markets = []
# Cache for Polymarket events (multi-outcome arbitrage detection)
# Each entry: {id, slug, title, markets: [{conditionId, question, bestAsk, ...}, ...]}
_poly_events_cache = []
# Caches for cross-platform multi-outcome combo detection
_kalshi_raw_cache = []    # Raw Kalshi market dicts (before price filtering)
_predict_raw_cache = []   # Raw Predict.fun market dicts (before extreme-price filtering)
_predict_ob_cache = {}    # Predict.fun orderbooks {market_id: {'yes_ask': float, 'no_ask': float}}
_probable_raw_cache = []   # Raw Probable Markets events (for multi-outcome analysis)
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
    """将标题转为 URL slug — 简单版本，不去除停用词
    与 Predict.fun 实际 URL 保持一致：
      "Opensea FDV above ___ one day after launch?" → "opensea-fdv-above-one-day-after-launch"
      "2026 NBA Champion"                          → "2026-nba-champion"
    规则：全小写 → 空格/下划线 → 连字符 → 去除非字母数字 → 合并连字符
    """
    import re
    text = text.lower()
    text = re.sub(r'[_\s]+', '-', text)
    text = re.sub(r'[^a-z0-9-]', '', text)
    text = re.sub(r'-+', '-', text)
    text = text.strip('-')
    return text


def question_to_predict_slug(question_text):
    """将问题文本转换为 Predict.fun 风格的 slug

    核心原则：URL 只包含父市场/预测主体，不包含子市场选项

    Predict.fun 的市场类型和 URL 规则：
    1. "by ___" 填空型市场（子市场是具体日期/时间）：
       - "Will Opinion launch a token by March?" → "will-opinion-launch-a-token-by"
       - "Will Opinion launch a token by June?" → "will-opinion-launch-a-token-by"（同父市场）
       - "Will Opinion launch a token by end of 2025?" → "will-opinion-launch-a-token-by"
       - 规则：保留 "by"，移除后面的具体月份/日期

    2. "above/below ___" 阈值型市场（子市场是具体数值）：
       - "Bitcoin price above $100k?" → "bitcoin-price-above"
       - 规则：保留 "above/below"，移除具体数值

    3. 竞猜类市场（子市场是候选人/队伍）：
       - "Will England win the 2026 FIFA World Cup?" → "2026-fifa-world-cup-winner"
       - 规则：移除候选人/队伍，保留事件+动词名词化

    4. "in ___" 时间型市场（子市场是年份）：
       - "Will BTC hit $100k in 2025?" → "will-btc-hit-100k-in"
       - 规则：保留 "in"，移除具体年份
    """
    import re

    if not question_text:
        return ''

    text = question_text.lower().strip()
    # 移除末尾的问号
    text = text.rstrip('?').strip()

    # === 模式1: "by [具体日期/时间]" 填空型市场 ===
    # "Will Opinion launch a token by March?" → "will-opinion-launch-a-token-by"
    # "Will Opinion launch a token by end of 2025?" → "will-opinion-launch-a-token-by"
    # 保留 "by"，移除后面的任何时间表达
    # 匹配: by [month], by end of [year/quarter], by [date], by middle/start/end of...
    by_pattern = r'^(.+?)\s+by\s+(?:.+?)\s*$'
    match = re.match(by_pattern, text)
    if match:
        base = match.group(1).strip()
        # 检查后面是否跟着时间相关词汇（月份、季度、年份等）
        after_by = text[len(base):-2].strip()  # 去掉 "by " 后的部分
        time_keywords = [
            'january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
            'september', 'october', 'november', 'december', 'jan', 'feb', 'mar', 'apr',
            'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec',
            'q1', 'q2', 'q3', 'q4', 'end', 'middle', 'start', 'beginning'
        ]
        # 如果 by 后面包含时间关键词，则认为是填空型市场
        if any(kw in after_by for kw in time_keywords) or re.search(r'\d{4}', after_by):
            return f"{slugify(base)}-by"

    # === 模式2: "in [具体年份]" 时间型市场 ===
    # "Will BTC hit $100k in 2025?" → "will-btc-hit-100k-in"
    in_pattern = r'^(.+?)\s+in\s+\d{4}\s*$'
    match = re.match(in_pattern, text)
    if match:
        base = match.group(1).strip()
        return f"{slugify(base)}-in"

    # === 模式3: 竞猜类 "Will [Subject] [verb] [object]?" ===
    # "Will England win the 2026 FIFA World Cup?" → "2026-fifa-world-cup-winner"
    win_pattern = r'^will\s+([a-z\s]+?)\s+(win|be|become|take|get)\s+(.+)$'
    match = re.match(win_pattern, text)
    if match:
        subject = match.group(1).strip()
        verb = match.group(2)
        obj = match.group(3).strip()

        # 移除常见的冠词
        obj = re.sub(r'^(the|a|an)\s+', '', obj)

        # 检查 object 是否以 "in/by" 结尾（如 "President in 2025"）
        # 如果是，需要处理成填空型
        if re.search(r'\s+(?:in|by)\s+\d{4}\s*$', obj):
            obj = re.sub(r'\s+(?:in|by)\s+\d{4}\s*$', '', obj)
            suffix = 'in' if ' in ' in text else 'by'
            base_slug = slugify(obj)
            return f"{base_slug}-{suffix}"

        # 动词名词化映射
        verb_noun_map = {
            'win': 'winner',
            'be': '',
            'become': '',
            'take': 'taker',
            'get': 'getter',
        }

        noun_suffix = verb_noun_map.get(verb, '')
        base_slug = slugify(obj)

        if noun_suffix:
            result_slug = f"{base_slug}-{noun_suffix}" if base_slug else noun_suffix
        else:
            result_slug = base_slug

        return result_slug

    # === 模式4: 阈值类 "[Subject] [above/below] [value]?" ===
    # 先移除 $数值 格式（$100k, $3B, $1.5M）
    text_without_values = re.sub(r'\$[\d.]+[bkmbtkmg]?', '', text, flags=re.IGNORECASE)
    # 移除剩余的纯数字（包括带百分号的）
    text_without_values = re.sub(r'\b\d+\.?\d*%?\b', '', text_without_values)
    # 清理多余空格
    text_without_values = re.sub(r'\s+', ' ', text_without_values).strip()

    # 清理并生成 slug
    result = slugify(text_without_values)

    # 如果结果为空或太短，回退到原始文本的 slugify
    if len(result) < 5:
        result = slugify(question_text)

    return result


def platform_link_html(platform_name, market_url=None):
    """Generate colored platform link HTML"""
    platform_colors = {
        'Polymarket': '#58a6ff',
        'Opinion': '#d29922',
        'Opinion.trade': '#d29922',
        'Predict': '#9c27b0',
        'Predict.fun': '#9c27b0',
        'Kalshi': '#3fb950',
        'Probable': '#ff6b6b',
        'Probable.market': '#ff6b6b',
    }
    platform_urls = {
        'Polymarket': 'https://polymarket.com',
        'Opinion': 'https://opinion.trade',
        'Opinion.trade': 'https://opinion.trade',
        'Predict': 'https://predict.fun',
        'Predict.fun': 'https://predict.fun',
        'Kalshi': 'https://kalshi.com',
        'Probable': 'https://probable.markets',
        'Probable.market': 'https://probable.markets',
    }
    color = platform_colors.get(platform_name, '#888')
    url = market_url if market_url else platform_urls.get(platform_name, '#')
    return f"<a href='{url}' target='_blank' style='color:{color};font-weight:600;text-decoration:none'>{platform_name}</a>"


def fetch_polymarket_data(config):
    """Fetch Polymarket markets - use best_ask (卖一价) for accurate arbitrage pricing"""
    global _poly_raw_markets, _poly_events_cache
    try:
        from src.polymarket_api import PolymarketClient
        poly_client = PolymarketClient(config)
        # Full-site paginated fetch — Polymarket has ~28k active markets
        markets = poly_client.get_markets(limit=POLYMARKET_FETCH_LIMIT, active_only=True)

        # Cache raw markets for cross-platform matching
        _poly_raw_markets = markets

        # Also fetch events for multi-outcome arbitrage.
        # The /events endpoint guarantees ALL sub-markets of each event are returned,
        # whereas /markets only returns top-volume markets so multi-outcome event
        # sub-markets may be missing from the batch.
        try:
            events = poly_client.get_events(limit=200, active_only=True)
            _poly_events_cache = events
            logger.info(f"Polymarket events: {len(events)} events (multi-outcome analysis)")
        except Exception as ev_err:
            logger.warning(f"Polymarket events fetch failed (multi-outcome disabled): {ev_err}")
            _poly_events_cache = []

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

                # Polymarket 正确链接格式: https://polymarket.com/event/{event_slug}
                # 必须用 events[0] 的 slug（事件级），NOT market 自身的 slug（子问题级）
                # 例：event slug = "2026-fifa-world-cup-winner"
                #     → https://polymarket.com/event/2026-fifa-world-cup-winner
                event = events[0] if events else {}
                event_slug = event.get('slug', '')
                if event_slug:
                    market_slug = event_slug
                else:
                    # 回退：使用 market 本身的 slug 或 conditionId
                    market_slug = m.get('slug', '') or condition_id

                parsed.append({
                    'id': condition_id,
                    'title': f"<a href='https://polymarket.com/event/{market_slug}' target='_blank' style='color:#03a9f4;font-weight:600'>{question[:80]}</a>",
                    'url': f"https://polymarket.com/event/{market_slug}",
                    'yes': round(yes_price, 4),
                    'no': round(no_price, 4),
                    'ask_size': 0,  # Polymarket API 不提供 ask_size
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
    """Fetch Opinion.trade markets — use best_ask for BOTH Yes and No tokens.

    Previously only the Yes token orderbook was fetched and No was derived as
    (1 - yes_mid).  That derivation always sums to exactly 100c, making it
    impossible to detect real arbitrage spreads.  Now both tokens are fetched
    concurrently and their actual ask prices are used directly.

    Fallback: if the No orderbook call fails, No price is derived from the
    Yes mid-price as a last resort (better than dropping the market entirely).
    """
    api_key = config.get('opinion', {}).get('api_key', '')
    if not api_key:
        logger.warning("Opinion: no API key")
        return 'no_key', []

    try:
        from src.opinion_api import OpinionAPIClient
        client = OpinionAPIClient(config)

        # Single sort by 24h volume — Opinion has ~150 total markets, one fetch covers all
        raw_markets = client.get_markets(status='activated', sort_by=5, limit=OPINION_MARKET_LIMIT)

        if not raw_markets:
            return 'error', []

        logger.info(f"Opinion: {len(raw_markets)} raw markets, fetching Yes+No orderbook asks...")

        # Filter markets that have both token IDs
        valid_markets = []
        for m in raw_markets:
            if m.get('yesTokenId') and m.get('noTokenId'):
                valid_markets.append(m)
        logger.info(f"Opinion: {len(valid_markets)} markets have yes+no tokens")

        # Fetch BOTH Yes and No token orderbooks concurrently.
        # 分页已有速率控制 (100ms/页)，这里不再额外等待。
        # token_prices: token_id -> (ask, bid)
        token_prices = {}
        _op_call_count = [0]
        _op_lock = threading.Lock()

        def fetch_token_orderbook(token_id):
            """Fetch orderbook for one token; return (token_id, ask, bid, ask_size)."""
            with _op_lock:
                _op_call_count[0] += 1
                # 每 12 个请求暂停 1s → 稳定 ~12 req/s (API 限制 15)
                if _op_call_count[0] % 12 == 0:
                    time.sleep(1.0)
            ob = client.get_order_book(token_id)
            if ob is not None and ob.yes_ask > 0:
                ask = ob.yes_ask
                bid = ob.yes_bid if ob.yes_bid > 0 else 0.0
                ask_size = ob.yes_ask_size if ob.yes_ask_size > 0 else 0
                return token_id, ask, bid, ask_size
            return token_id, None, None, 0

        # Collect both Yes and No token IDs
        tokens_to_fetch = []
        for m in valid_markets:
            tokens_to_fetch.append(m['yesTokenId'])
            tokens_to_fetch.append(m['noTokenId'])

        logger.info(f"Opinion: fetching {len(tokens_to_fetch)} token orderbooks "
                    f"({OPINION_FETCH_WORKERS} workers, ~12 req/s)")

        with ThreadPoolExecutor(max_workers=OPINION_FETCH_WORKERS, thread_name_prefix='op-ob') as ex:
            futures = {ex.submit(fetch_token_orderbook, t): t for t in tokens_to_fetch}
            for future in as_completed(futures, timeout=240):
                try:
                    token_id, ask, bid, ask_size = future.result(timeout=15)
                    if ask is not None:
                        token_prices[token_id] = (ask, bid, ask_size)
                except Exception:
                    pass

        yes_hits = sum(1 for m in valid_markets if m['yesTokenId'] in token_prices)
        no_hits  = sum(1 for m in valid_markets if m['noTokenId']  in token_prices)
        logger.info(f"Opinion: {yes_hits} Yes asks, {no_hits} No asks (first pass)")

        # Retry: 等 2s 让限流窗口重置，重跑一次失败的
        failed_tokens = [t for t in tokens_to_fetch if t not in token_prices]
        if failed_tokens:
            logger.info(f"Opinion: {len(failed_tokens)} failed, retrying in 2s...")
            time.sleep(2.0)
            _op_call_count[0] = 0
            with ThreadPoolExecutor(max_workers=OPINION_FETCH_WORKERS, thread_name_prefix='op-retry') as ex:
                futures = {ex.submit(fetch_token_orderbook, t): t for t in failed_tokens}
                for future in as_completed(futures, timeout=120):
                    try:
                        token_id, ask, bid, ask_size = future.result(timeout=15)
                        if ask is not None:
                            token_prices[token_id] = (ask, bid, ask_size)
                    except Exception:
                        pass

            yes_hits = sum(1 for m in valid_markets if m['yesTokenId'] in token_prices)
            no_hits  = sum(1 for m in valid_markets if m['noTokenId']  in token_prices)
            logger.info(f"Opinion: {yes_hits} Yes asks, {no_hits} No asks (after retry)")

        # Build parsed list using actual ask prices
        parsed = []
        for m in valid_markets:
            try:
                market_id  = str(m.get('marketId', ''))
                title      = m.get('marketTitle', '')
                yes_token  = m['yesTokenId']
                no_token   = m['noTokenId']

                yes_data = token_prices.get(yes_token)
                if yes_data is None:
                    continue  # Can't price Yes → skip market

                yes_ask, yes_bid, yes_ask_size = yes_data

                no_data = token_prices.get(no_token)
                if no_data is not None:
                    no_ask = no_data[0]  # ✅ actual No ask
                else:
                    # Fallback: derive No from Yes mid when No orderbook unavailable
                    yes_mid = (yes_bid + yes_ask) / 2 if yes_bid > 0 else yes_ask
                    no_ask  = round(1.0 - yes_mid, 4)

                # Sanity bounds
                if yes_ask <= 0 or yes_ask >= 1 or no_ask <= 0 or no_ask >= 1:
                    continue

                parsed.append({
                    'id':        market_id,
                    'title':     f"<a href='https://app.opinion.trade/detail?topicId={market_id}' "
                                 f"target='_blank' style='color:#d29922;font-weight:600'>{title[:80]}</a>",
                    'url':       f"https://app.opinion.trade/detail?topicId={market_id}",
                    'yes':       round(yes_ask, 4),
                    'no':        round(no_ask,  4),
                    'ask_size':  yes_ask_size,  # 可购买的股数
                    'volume':    float(m.get('volume24h', m.get('volume', 0)) or 0),
                    'liquidity': 0,
                    'platform':  'opinion',
                    'end_date':  m.get('cutoff_at', ''),
                })
            except (ValueError, TypeError, KeyError):
                continue
            except Exception as e:
                logger.warning(f"Opinion parse error: {e}")
                continue

            if len(parsed) >= OPINION_PARSED_LIMIT:
                break

        logger.info(f"Opinion: {len(parsed)} markets (Yes ask + No ask pricing)")
        return 'active', parsed
    except ImportError as e:
        logger.error(f"Opinion import error: {e}")
        return 'error', []
    except Exception as e:
        logger.error(f"Opinion fetch error: {e}")
        return 'error', []


def fetch_predict_data(config):
    """Fetch ALL Predict.fun open markets via cursor pagination + concurrent orderbook"""
    global _predict_raw_cache, _predict_ob_cache
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
        # Predict v1 API uses 'cursor' response field, 'after' query parameter
        # Increased max pages (20) to ensure full market coverage
        session = req.Session()
        session.headers.update({'x-api-key': api_key, 'Content-Type': 'application/json'})

        all_raw = []
        seen_ids = set()
        cursor = None

        for page in range(PREDICT_FETCH_MAX_PAGES):
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

        # === Phase 2: Extract inline orderbooks + concurrent fetch for missing ===
        orderbook_results = {}

        # First: extract orderbooks already embedded in market list response
        # This avoids unnecessary HTTP calls for markets that include orderBook data
        markets_needing_fetch = []
        for m in all_raw:
            mid = m.get('id', m.get('market_id', ''))
            if not mid:
                continue

            ob_data = m.get('orderBook', {})
            if ob_data:
                bids = ob_data.get('bids', [])
                asks = ob_data.get('asks', [])
                if bids and asks:
                    try:
                        # Parse inline orderbook — same logic as PredictAPIClient._get_orderbook
                        first_bid = bids[0]
                        first_ask = asks[0]
                        if isinstance(first_bid, dict):
                            yes_bid = float(first_bid.get('price', first_bid.get('p', 0)))
                        elif isinstance(first_bid, (list, tuple)):
                            yes_bid = float(first_bid[0])
                        else:
                            yes_bid = float(first_bid)

                        if isinstance(first_ask, dict):
                            yes_ask = float(first_ask.get('price', first_ask.get('p', 0)))
                            ask_size = float(first_ask.get('quantity', first_ask.get('amount', first_ask.get('q', first_ask.get('size', 0)))))
                        elif isinstance(first_ask, (list, tuple)):
                            yes_ask = float(first_ask[0])
                            ask_size = float(first_ask[1]) if len(first_ask) > 1 else 0
                        else:
                            yes_ask = float(first_ask)
                            ask_size = 0

                        if yes_bid > 0 and yes_ask > 0:
                            orderbook_results[mid] = {
                                'yes_bid': yes_bid,
                                'yes_ask': yes_ask,
                                'no_bid': round(1.0 - yes_ask, 4),
                                'no_ask': round(1.0 - yes_bid, 4),
                                'ask_size': ask_size,
                            }
                            continue
                    except (ValueError, TypeError, IndexError, KeyError):
                        pass

            markets_needing_fetch.append(mid)

        logger.info(f"Predict: {len(orderbook_results)} inline orderbooks, "
                    f"{len(markets_needing_fetch)} need separate fetch")

        def fetch_orderbook(market_id):
            full_ob = client.get_full_orderbook(market_id)
            return market_id, full_ob

        # Concurrent fetch for markets without inline orderbook data
        if markets_needing_fetch:
            logger.info(f"Predict: fetching {len(markets_needing_fetch)} orderbooks "
                        f"with {PREDICT_ORDERBOOK_WORKERS} workers...")

            with ThreadPoolExecutor(max_workers=PREDICT_ORDERBOOK_WORKERS,
                                    thread_name_prefix='pred-ob') as ex:
                futures = {ex.submit(fetch_orderbook, mid): mid
                           for mid in markets_needing_fetch}
                for future in as_completed(futures, timeout=180):
                    try:
                        mid, ob = future.result(timeout=15)
                        if ob is not None:
                            orderbook_results[mid] = ob
                    except Exception:
                        pass

        logger.info(f"Predict: got {len(orderbook_results)} orderbooks (first pass)")

        # Retry pass for failed orderbook fetches (with delay)
        failed_ids = [mid for mid in markets_needing_fetch
                      if mid not in orderbook_results]
        if failed_ids:
            logger.info(f"Predict: {len(failed_ids)} failed, waiting 2s before retry...")
            time.sleep(2.0)
            with ThreadPoolExecutor(max_workers=PREDICT_ORDERBOOK_WORKERS,
                                    thread_name_prefix='pred-retry') as ex:
                futures = {ex.submit(fetch_orderbook, mid): mid
                           for mid in failed_ids}
                for future in as_completed(futures, timeout=120):
                    try:
                        mid, ob = future.result(timeout=15)
                        if ob is not None:
                            orderbook_results[mid] = ob
                    except Exception:
                        pass
            logger.info(f"Predict: {len(orderbook_results)} orderbooks after retry")
        _predict_raw_cache = all_raw          # store for cross-platform combo analysis
        _predict_ob_cache = orderbook_results

        # 调试：打印第一个市场的完整字段和值，找出正确的父市场 slug 字段名
        if all_raw:
            sample = all_raw[0]
            logger.info(f"Predict DEBUG - sample market full data: {sample}")

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
                ask_size = ob.get('ask_size', 0) or 0  # 可购买的股数
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
                # Predict.fun 链接格式: https://predict.fun/market/{父市场slug}
                # 注意：m['slug'] 是子结果 slug（如 "england"），不能用
                # 优先从 API 字段里找父市场 slug，否则从问题文本推导
                parent_slug = (
                    m.get('groupSlug') or
                    m.get('parentSlug') or
                    m.get('eventSlug') or
                    m.get('marketSlug') or
                    m.get('market_slug') or
                    m.get('group_slug') or
                    m.get('parent_slug')
                )
                if parent_slug:
                    market_slug = parent_slug
                    logger.debug(f"Predict slug from parent field: {market_slug}")
                else:
                    # 没有找到父市场 slug 字段——用智能 slug 生成兜底
                    # question_to_predict_slug 会根据问题类型智能生成 slug
                    market_slug = question_to_predict_slug(question_text)
                    logger.debug(f"Predict slug from question ({question_text[:40]}...): {market_slug}")
                parsed.append({
                    'id': market_id,
                    'title': f"<a href='https://predict.fun/market/{market_slug}' target='_blank' style='color:#9c27b0;font-weight:600'>{question_text[:80]}</a>",
                    'url': f"https://predict.fun/market/{market_slug}",
                    'yes': round(yes_price, 4),
                    'no': round(no_price, 4),
                    'ask_size': ask_size,  # 可购买的股数
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

        logger.info(f"Predict: {len(parsed)} markets with prices "
                    f"({extreme_count} extreme filtered, {len(all_raw)} raw, "
                    f"{len(orderbook_results)} OBs)")
        return 'active', parsed
    except ImportError as e:
        logger.error(f"Predict import error: {e}")
        return 'error', []
    except Exception as e:
        logger.error(f"Predict fetch error: {e}")
        return 'error', []


def fetch_kalshi_data(config):
    """Fetch Kalshi markets — prices included in /markets response (no orderbook calls)"""
    global _kalshi_raw_cache
    try:
        from src.kalshi_api import KalshiClient
        client = KalshiClient(config)
        raw_markets = client.get_markets(status='open', limit=KALSHI_FETCH_LIMIT)
        logger.info(f"Kalshi: fetched {len(raw_markets)} raw markets")
        _kalshi_raw_cache = raw_markets  # store for cross-platform multi-outcome analysis

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
                    'title': f"<a href='{url}' target='_blank' style='color:#3fb950;text-decoration:none'>{title[:80]}</a>",
                    'url': url,
                    'match_title': title,
                    'yes': round(yes_ask, 4),
                    'no': round(no_ask, 4),
                    'ask_size': 0,  # Kalshi API 不提供 ask_size，需单独调用 orderbook API
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


def fetch_probable_data(config):
    """Fetch Probable Markets — using probable_api client with pagination"""
    global _probable_raw_cache
    try:
        from src.probable_api import ProbableClient
        client = ProbableClient(config)

        # Get all events with pagination, then extract markets
        events = client.get_events(active_only=True, limit=PROBABLE_FETCH_LIMIT)
        logger.info(f"Probable: fetched {len(events)} events")

        # Cache raw events for cross-platform multi-outcome analysis
        _probable_raw_cache = events

        # First pass: collect markets and their token IDs
        markets_to_fetch = []
        for event in events:
            for market in event.get('markets', []):
                try:
                    market_id = str(market.get('id', ''))
                    question = market.get('question', '')
                    liquidity = float(market.get('liquidity', 0) or 0)
                    volume_24h = float(market.get('volume24hr', 0) or 0)
                    end_date = market.get('endDate', '')

                    # Get token IDs for Yes/No
                    tokens = market.get('tokens', [])
                    if len(tokens) < 2:
                        continue

                    yes_token_id = tokens[0].get('token_id')
                    no_token_id = tokens[1].get('token_id')

                    if not yes_token_id or not no_token_id:
                        continue

                    event_slug = event.get('slug', '')
                    url = f"https://probable.markets/event/{event_slug}" if event_slug else "https://probable.markets"

                    markets_to_fetch.append({
                        'market_id': market_id,
                        'question': question,
                        'liquidity': liquidity,
                        'volume_24h': volume_24h,
                        'end_date': end_date,
                        'yes_token_id': str(yes_token_id),
                        'no_token_id': str(no_token_id),
                        'url': url,
                        'event_slug': event_slug,
                    })
                except (ValueError, TypeError, KeyError):
                    continue

        # Batch fetch prices
        token_pairs = [{'token_id': m['yes_token_id'], 'side': 'BUY'} for m in markets_to_fetch] + \
                      [{'token_id': m['no_token_id'], 'side': 'BUY'} for m in markets_to_fetch]
        price_data = client.get_token_prices_batch(token_pairs) if token_pairs else {}

        # Concurrently fetch orderbook ask sizes for yes tokens
        yes_token_ids = list({m['yes_token_id'] for m in markets_to_fetch})
        orderbook_data = {}

        def fetch_orderbook(token_id):
            try:
                book = client.get_order_book_by_token_id(token_id)
                return token_id, book
            except Exception as e:
                logger.debug(f"Failed to fetch orderbook for {token_id}: {e}")
                return token_id, None

        if yes_token_ids:
            with ThreadPoolExecutor(max_workers=20) as executor:
                futures = {executor.submit(fetch_orderbook, tid): tid for tid in yes_token_ids}
                for future in as_completed(futures):
                    token_id, book = future.result()
                    if book:
                        orderbook_data[token_id] = book

        # Build parsed list
        parsed = []
        for m in markets_to_fetch:
            yes_str_id = m['yes_token_id']
            no_str_id = m['no_token_id']

            yes_price = float(price_data.get(yes_str_id, {}).get('BUY', 0.5)) if yes_str_id in price_data else 0.5
            no_price = float(price_data.get(no_str_id, {}).get('BUY', 0.5)) if no_str_id in price_data else 0.5

            # Get ask_size from orderbook
            ask_size = 0
            if yes_str_id in orderbook_data:
                try:
                    ask_size = int(orderbook_data[yes_str_id].get('best_ask_size', 0) or 0)
                except (ValueError, TypeError):
                    ask_size = 0

            # Filter extreme prices
            if yes_price < PREDICT_EXTREME_FILTER or yes_price > (1 - PREDICT_EXTREME_FILTER):
                continue

            parsed.append({
                'id': m['market_id'],
                'title': f"<a href='{m['url']}' target='_blank' style='color:#f85149;text-decoration:none'>{m['question'][:80]}</a>",
                'url': m['url'],
                'match_title': m['question'],
                'yes': round(yes_price, 4),
                'no': round(no_price, 4),
                'ask_size': ask_size,
                'volume': m['volume_24h'],
                'liquidity': m['liquidity'],
                'platform': 'probable',
                'end_date': m['end_date'],
            })

        # Sort by liquidity
        parsed.sort(key=lambda x: x['liquidity'], reverse=True)
        logger.info(f"Probable: {len(parsed)} markets with valid prices")
        return 'active', parsed
    except ImportError as e:
        logger.error(f"Probable import error: {e}")
        return 'error', []
    except Exception as e:
        logger.error(f"Probable fetch error: {e}")
        return 'error', []


def update_price_history(arbitrage_list):
    """Track price history for arbitrage opportunities (last N data points per market)"""
    now_str = datetime.now(_TZ_CST).strftime('%H:%M:%S')
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
    skipped_price_sanity = 0

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
        min_similarity=0.60,
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
                if time_diff > 60:
                    skipped_end_date += 1
                    continue
            except Exception:
                pass

        # Price sanity check: genuine matched markets must roughly agree on probability.
        # If Yes prices diverge by >40c they are almost certainly asking OPPOSITE or
        # completely unrelated questions (e.g. "Trump out?" at 3c vs "Trump remain?" at 54c).
        # No legitimate cross-platform arb survives a 40c directional gap.
        if abs(ma['yes'] - mb['yes']) > 0.40:
            skipped_price_sanity += 1
            continue

        combined1 = ma['yes'] + mb['no']
        arb1 = (1.0 - combined1) * 100

        combined2 = mb['yes'] + ma['no']
        arb2 = (1.0 - combined2) * 100

        market_key_base = f"{platform_a_name}-{platform_b_name}-{ma.get('id','')}-{mb.get('id','')}"
        now_str = datetime.now(_TZ_CST).strftime('%H:%M:%S')

        fee_a = PLATFORM_FEES.get(platform_a_name.lower(), 0.02)
        fee_b = PLATFORM_FEES.get(platform_b_name.lower(), 0.02)

        # 计算 shares：两个平台的 ask_size 之和
        ask_size_a = ma.get('ask_size', 0) or 0
        ask_size_b = mb.get('ask_size', 0) or 0
        shares = ask_size_a + ask_size_b if (ask_size_a > 0 and ask_size_b > 0) else (ask_size_a if ask_size_a > 0 else ask_size_b)

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
                'shares': int(shares) if shares > 0 else None,  # 可购买的股数
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
                'shares': int(shares) if shares > 0 else None,  # 可购买的股数
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
        f"Skipped(price_sanity): {skipped_price_sanity}, "
        f"Found: {len(opportunities)}"
    )

    return opportunities


def find_same_platform_arbitrage(markets, platform_name, threshold=0.5):
    """Detect same-platform arbitrage: Yes_ask + No_ask < $1.00
    If you can buy both Yes and No for less than $1, guaranteed profit on resolution.
    """
    opportunities = []
    fee_rate = PLATFORM_FEES.get(platform_name.lower(), 0.02)
    now_str = datetime.now(_TZ_CST).strftime('%H:%M:%S')

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

            # 同平台套利 shares = ask_size（同一市场的 Yes ask_size）
            ask_size = m.get('ask_size', 0) or 0

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
                'shares': int(ask_size) if ask_size > 0 else None,  # 可购买的股数
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


def find_polymarket_multi_outcome_arbitrage(poly_events, threshold=0.5):
    """检测 Polymarket 同平台多结果套利机会。

    使用 /events API（而非 /markets 分组）确保每个事件的所有子市场都被完整获取。
    Polymarket 事件中有且仅有一个结果会解析为 $1，因此若所有 Yes-ask 之和 < $1
    则购买全部结果是无风险套利。

    例：事件有 A(0.50), B(0.40), C(0.05) 三个结果
        总成本 = 0.95 < $1 → 套利利润 5%

    Args:
        poly_events: 从 Polymarket /events API 获取的事件列表
                     （每个事件包含 markets[] 数组）
        threshold:   最低套利百分比阈值（默认 0.5%）

    Returns:
        套利机会列表，按利润降序排列
    """
    if not poly_events:
        return []

    opportunities = []
    fee_rate = PLATFORM_FEES.get('polymarket', 0.02)
    now_str = datetime.now(_TZ_CST).strftime('%H:%M:%S')
    events_checked = 0
    events_with_3plus = 0

    for event in poly_events:
        sub_markets = event.get('markets', [])
        if len(sub_markets) < 3:
            continue

        events_checked += 1
        events_with_3plus += 1

        event_id = event.get('id', '')
        event_slug = event.get('slug', str(event_id))
        event_title = event.get('title', event_slug)
        event_url = f"https://polymarket.com/event/{event_slug}"

        now_utc = datetime.now(timezone.utc)
        outcomes = []
        for m in sub_markets:
            # 过滤已结算的子市场：closed=True 表示该子市场已提前结算
            # 即使父事件仍 active，个别子市场可能已 resolved（如 "by Dec 31, 2025"）
            if m.get('closed') is True or m.get('active') is False:
                continue

            # 过滤已过期的子市场（截止时间已过）
            # "Kraken IPO in 2025?" 类型的市场在 2026 年仍会出现在 API 中，
            # 但其截止时间已过，价格接近 0，不可再交易，应剔除。
            end_date_str = m.get('endDate') or m.get('endDateIso', '')
            if end_date_str:
                try:
                    end_dt = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                    if end_dt < now_utc:
                        continue  # 截止时间已过，跳过
                except Exception:
                    pass  # 日期解析失败则不过滤，保守处理

            # 优先使用 bestAsk（实际挂单买价）
            best_ask = m.get('bestAsk')
            yes_price = None
            if best_ask is not None:
                try:
                    yes_price = float(best_ask)
                except (ValueError, TypeError):
                    yes_price = None

            # 回退：使用 outcomePrices[0]
            if yes_price is None or yes_price <= 0:
                outcome_str = m.get('outcomePrices', '[]')
                try:
                    prices = json.loads(outcome_str) if isinstance(outcome_str, str) else outcome_str
                    if prices:
                        yes_price = float(prices[0])
                except Exception:
                    yes_price = None

            # 跳过无效价格（已结算为0或未定价）；低概率但仍交易中的子市场（如0.3c）必须保留，
            # 否则遗漏它们会使剩余选项合计 < $1，产生虚假套利信号
            if yes_price is None or yes_price <= 0 or yes_price >= 1:
                continue

            question = m.get('question', '')
            # 多结果市场的各选项都指向同一个 event URL
            outcomes.append({
                'name': question[:60],
                'price': round(yes_price, 4),
                'url': event_url,
            })

        # 需要至少 3 个有效价格的结果
        if len(outcomes) < 3:
            continue

        total_cost = sum(o['price'] for o in outcomes)

        # MECE 完整性检验：若总成本极端偏低，说明结果集不完整（非 MECE），剔除。
        # 合法的选举/冠军类事件，各结果之和应接近 $1（仅有小幅套利缺口）。
        # FDV 档位、独立二元市场等非完整集合，总和可能仅有 10–30c，
        # 此时不能保证有且仅有一个结果兑付 $1，买入所有结果并非无风险套利。
        if total_cost < MULTI_OUTCOME_MIN_TOTAL_COST:
            continue

        if total_cost >= 0.98:
            continue  # 扣除手续费后无利润（98c + 2% fee ≈ 100c）

        gross_pct = (1.0 - total_cost) * 100
        if gross_pct < threshold:
            continue

        # 每条腿收取 2% 手续费
        fee_cost = sum(o['price'] * fee_rate for o in outcomes) * 100
        net_pct = gross_pct - fee_cost

        # 计算 shares：多结果套利受限于最小 ask_size（需同时购买所有结果）
        ask_sizes = [o.get('ask_size', 0) or 0 for o in outcomes]
        valid_sizes = [s for s in ask_sizes if s > 0]
        shares = min(valid_sizes) if valid_sizes else 0

        market_key = f"MULTI-poly-{event_id}"
        opportunities.append({
            'event_title': event_title,
            'event_url': event_url,
            'platform': 'Polymarket',
            'platform_color': '#03a9f4',
            'outcomes': outcomes,
            'outcome_count': len(outcomes),
            'total_cost': round(total_cost * 100, 2),
            'arbitrage': round(gross_pct, 2),
            'shares': int(shares) if shares > 0 else None,  # 可购买的套利组合数（受限于最小 ask_size）
            'net_profit': round(net_pct, 2),
            'timestamp': now_str,
            'market_key': market_key,
            '_created_at': time.time(),
            'arb_type': 'multi_outcome',
        })

    opportunities.sort(key=lambda x: x['arbitrage'], reverse=True)
    logger.info(
        f"[Polymarket MULTI] {len(poly_events)} events → "
        f"{events_with_3plus} with 3+ outcomes → "
        f"{len(opportunities)} arb found"
    )
    return opportunities


# ============================================================
# Logical Spread Arbitrage (同平台逻辑价差套利)
# ============================================================

def _extract_key_differences(hard_title: str, easy_title: str) -> dict:
    """
    提取两个市场标题中的关键差异（时间/价格关键词）

    例如：
    - "Ukraine... by June 30" vs "Ukraine... by December 31"
      → {'hard': 'by June 30', 'easy': 'by December 31'}
    - "BTC top $100k" vs "BTC top $50k"
      → {'hard': '>$100K', 'easy': '>$50K'}
    """
    import re

    # 提取价格关键词（优先级最高）
    price_patterns = [
        r'\$[\d,]+(?:\.\d+)?[kmbt](?:\s|$)',  # $100k, $1.5M, $1T (with suffix)
        r'\$[\d,]+(?:\.\d+)?\s*(?:k|m|b|t)(?:\s|$)',  # alternate $100 k format
        r'\$[\d,]+(?:\.\d+)?(?=\s|,|$)',  # $100,000 (no suffix)
        r'(?:top|above|over|surpass|exceed|reach|hit)\s+\$[\d,]+(?:\.\d+)?[kmbt]?',  # top $100k
        r'(?:below|under|dip|drop|fall)\s+\$[\d,]+(?:\.\d+)?[kmbt]?',  # below $50k
        r'(?:between|from)\s+\$[\d,]+[kmbt]?\s*(?:and|to|-)\s*\$[\d,]+[kmbt]?',  # between $X and $Y
        r'(?:less|more|than)\s+[\d,]+(?:\.\d+)?[kmbt]?',  # less than 250k
        r'[\d,]+(?:\.\d+)?[kmbt]?\s*(?:to|-)\s*[\d,]+(?:\.\d+)?[kmbt]?',  # 250k-500k
    ]

    # 提取日期关键词
    date_patterns = [
        r'(?:by|before|after|until)\s+(?:[A-Za-z]+\s+)?\d{1,2}(?:st|nd|rd|th)?(?:,?\s+20\d{2})?',  # by June 30, 2026
        r'(?:by|before|after|until|in)\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,4}',  # by March 2026
        r'(?:by|before|after|until|in)\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)[a-z]*\s+\d{1,4}',
        r'(?:by|before|after|until|in)\s+(?:Q[1-4]|1Q|2Q|3Q|4Q)\s+\d{4}',  # by Q1 2026
        r'in\s+20\d{2}',  # in 2025
    ]

    # 提取阈值/数量关键词
    threshold_patterns = [
        r'(?:deport|arrest)\s+[\d,]+(?:\.\d+)?[kmb]?',
        r'[\d,]+(?:\.\d+)?[kmb]?\s*(?:people|seats|votes|individuals)',
    ]

    def extract_patterns(text, patterns):
        """从文本中提取所有匹配的模式"""
        matches = []
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                matches.append(match.group(0))
        return matches

    # 优先提取价格差异
    hard_prices = extract_patterns(hard_title, price_patterns)
    easy_prices = extract_patterns(easy_title, price_patterns)

    if hard_prices and easy_prices:
        # 取最短的价格表示（更精确）
        hard_price = min(hard_prices, key=len)
        easy_price = min(easy_prices, key=len)
        return {
            'hard': hard_price,
            'easy': easy_price,
            'type': 'price'
        }

    # 尝试提取日期差异
    hard_dates = extract_patterns(hard_title, date_patterns)
    easy_dates = extract_patterns(easy_title, date_patterns)

    if hard_dates and easy_dates:
        # 取最短的日期表示
        hard_date = min(hard_dates, key=len)
        easy_date = min(easy_dates, key=len)
        return {
            'hard': hard_date,
            'easy': easy_date,
            'type': 'time'
        }

    # 尝试提取阈值差异
    hard_thresholds = extract_patterns(hard_title, threshold_patterns)
    easy_thresholds = extract_patterns(easy_title, threshold_patterns)

    if hard_thresholds and easy_thresholds:
        return {
            'hard': hard_thresholds[0],
            'easy': easy_thresholds[0],
            'type': 'threshold'
        }

    # 回退：从标题中提取简短的关键词
    # 尝试找到不同的部分
    hard_words = set(hard_title.lower().split())
    easy_words = set(easy_title.lower().split())

    # 找出硬文本中独有的词（作为关键词）
    hard_unique = ' '.join([w for w in hard_title.split() if w.lower() not in easy_words])
    easy_unique = ' '.join([w for w in easy_title.split() if w.lower() not in hard_words])

    return {
        'hard': hard_unique[:30] if hard_unique else hard_title[:30],
        'easy': easy_unique[:30] if easy_unique else easy_title[:30],
        'type': 'other'
    }


def find_logical_spread_arbitrage(events, platform_name='Polymarket', threshold=0.0):
    """检测同平台逻辑价差套利机会（基于事件架构）。

    对于具有逻辑包含关系的事件对：
    - Hard 事件：条件更严格（如 BTC>$100k）
    - Easy 事件：条件更宽松（如 BTC>$50k）
    - 正常情况：P(hard) < P(easy)
    - 套利机会：P(hard) >= P(easy)（市场倒挂或定价异常）

    新架构：基于 Polymarket /events 端点，在同一事件的子市场之间比较，
    避免跨事件错误匹配（如 Senate vs Trump）。

    Args:
        events: 从 /events API 获取的事件列表（每个事件包含 markets[]）
        platform_name: 平台名称
        threshold: 最小价差阈值（默认 0.0，即包括 P(hard)=P(easy)）

    Returns:
        套利机会列表
    """
    try:
        from src.logical_spread_arbitrage import LogicalSpreadArbitrageDetector
    except ImportError:
        logger.warning("Logical Spread Arbitrage module not available")
        return []

    if not events:
        return []

    config = {
        'logical_spread_arbitrage': {
            'min_spread_threshold': threshold,
            'fee_rate': PLATFORM_FEES.get(platform_name.lower(), 0.02),
            'min_threshold_diff_pct': 10.0,
        }
    }

    detector = LogicalSpreadArbitrageDetector(config)

    # 执行扫描（基于事件）
    arbitrage_pairs = detector.scan_events(events, platform_name.lower())

    # 转换为 dashboard 格式
    opportunities = []
    now_str = datetime.now(_TZ_CST).strftime('%H:%M:%S')

    for pair in arbitrage_pairs:
        spread_pct = pair.spread * 100
        if spread_pct < threshold:
            continue

        # 计算收益（不考虑手续费，只计算实际买入成本）
        gross_pct = pair.arbitrage_profit * 100
        # 不再扣除手续费：LSA 套利只看实际买入成本

        # 类型名称
        type_names = {
            'price_threshold': '价格阈值',
            'time_window': '时间窗口',
        }
        type_name = type_names.get(pair.logical_type.value, '未知类型')

        # 提取关键词差异（用于前端显示）
        key_differences = _extract_key_differences(pair.hard_title, pair.easy_title)

        # 构建 URL（使用 API 返回的 event slug）
        event_slug = pair.event_slug or pair.event_id
        event_url = f"https://polymarket.com/event/{event_slug}"

        # 盘口数据：bid / ask / spread（百分比格式，乘以100）
        def _fmt_price(v):
            return round(v * 100, 2) if v is not None else None

        # ask_profit: 基于 bestAsk 的实际利润（更保守）
        ask_profit_pct = round(pair.ask_profit * 100, 2) if pair.ask_profit else None

        opportunities.append({
            'type': type_name,
            'relationship': pair.relationship_desc,
            'hard_title': pair.hard_title[:70],
            'hard_yes': round(pair.hard_price * 100, 2),      # mid-price (主显示)
            'hard_id': pair.hard_market_id,
            'hard_url': f"{event_url}#{pair.hard_market_id[:16]}",
            'easy_title': pair.easy_title[:70],
            'easy_yes': round(pair.easy_price * 100, 2),      # mid-price (主显示)
            'easy_id': pair.easy_market_id,
            'easy_url': f"{event_url}#{pair.easy_market_id[:16]}",
            'cost': round(pair.arbitrage_cost * 100, 2),       # mid-price 理论成本
            'arbitrage': round(gross_pct, 2),                  # mid-price 理论利润
            'ask_profit': ask_profit_pct,                      # bestAsk 实际利润
            'signal_tier': pair.signal_tier,                   # 信号分层: executable/limit_candidate/monitor_only
            'platform': platform_name,
            'timestamp': now_str,
            'market_key': f"LSA-{platform_name.lower()}-{pair.pair_key}",
            '_created_at': time.time(),
            'arb_type': 'logical_spread',
            'event_title': pair.event_title[:70] if pair.event_title else '',
            'event_url': event_url,
            'key_differences': key_differences,
            'hard_market_tag': key_differences.get('hard', ''),
            'easy_market_tag': key_differences.get('easy', ''),
            # 盘口详细数据
            'hard_bid': _fmt_price(pair.hard_best_bid),
            'hard_ask': _fmt_price(pair.hard_best_ask),
            'hard_spread': _fmt_price(pair.hard_spread),
            'easy_bid': _fmt_price(pair.easy_best_bid),
            'easy_ask': _fmt_price(pair.easy_best_ask),
            'easy_spread': _fmt_price(pair.easy_spread),
            'hard_liq': pair.hard_has_liquidity,
            'easy_liq': pair.easy_has_liquidity,
        })

    # 排序: executable 优先，然后 limit_candidate，最后 monitor_only；同层按利润降序
    tier_order = {'executable': 0, 'limit_candidate': 1, 'monitor_only': 2}
    opportunities.sort(key=lambda x: (tier_order.get(x.get('signal_tier', 'monitor_only'), 9), -x['arbitrage']))
    if opportunities:
        tier_counts = {}
        for opp in opportunities:
            t = opp.get('signal_tier', 'monitor_only')
            tier_counts[t] = tier_counts.get(t, 0) + 1
        tier_str = ', '.join(f"{v} {k}" for k, v in tier_counts.items())
        logger.info(f"[{platform_name} LSA] Found {len(opportunities)} logical spread arbitrage ({tier_str})")

    return opportunities


def find_logical_spread_arbitrage_with_orderbook(events, platform_name='Polymarket', threshold=0.0):
    """使用订单簿数据检测逻辑价差套利机会（高级版本）。

    注意：此功能暂时禁用，回退到基础事件版本。
    订单簿监控模块需要更新以支持新的事件架构。

    Args:
        events: 从 /events API 获取的事件列表
        platform_name: 平台名称
        threshold: 最小价差阈值

    Returns:
        套利机会列表
    """
    # TODO: 更新 orderbook_monitor 以支持新的事件架构
    # 目前回退到基础版本
    logger.debug("[LSA-OB] Orderbook version disabled, using basic event-based version")
    return find_logical_spread_arbitrage(events, platform_name, threshold)


# ============================================================
# Cross-platform multi-outcome combo arbitrage
# ============================================================

def _extract_outcome_label(title):
    """从市场标题中提取结果标签（子市场名称）。

    Examples:
      "Brazil to win the 2026 FIFA World Cup"   → "Brazil"
      "Will England win the 2026 FIFA World Cup?" → "England"
      "Lakers to win the NBA Championship"        → "Lakers"
      "Biden wins the 2024 Presidential Election" → "Biden"
    """
    import re
    text = title.strip()
    # "Will [OUTCOME] win/beat/defeat/finish..."
    m = re.match(r'^will\s+(?:the\s+)?(.+?)\s+(?:win|beat|defeat|finish|become|be\s+(?:the|elected))',
                 text, re.IGNORECASE)
    if m:
        outcome = m.group(1).strip()
        if 1 <= len(outcome.split()) <= 4:
            return outcome
    # "[OUTCOME] to win/be/become/reach/finish..."
    m = re.match(r'^(.+?)\s+to\s+(?:win|be|become|reach|finish|make)', text, re.IGNORECASE)
    if m:
        outcome = m.group(1).strip()
        if 1 <= len(outcome.split()) <= 4:
            return outcome
    # "[OUTCOME] wins/gains/leads..."
    m = re.match(r'^(.+?)\s+(?:wins?|gains?|leads?|takes?)\b', text, re.IGNORECASE)
    if m:
        outcome = m.group(1).strip()
        if 1 <= len(outcome.split()) <= 4:
            return outcome
    # Fallback: first 1-3 words (outcome labels are usually short)
    words = text.split()
    return ' '.join(words[:min(3, len(words))])


def _normalize_title_for_matching(text):
    """标准化事件标题用于跨平台匹配（去除差异化词汇）。"""
    import re
    t = text.lower()
    t = re.sub(r'[-_]', ' ', t)
    t = re.sub(r'[^a-z0-9\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    # Remove stopwords that differ between platforms but don't affect meaning
    stopwords = {'the', 'a', 'an', 'will', 'who', 'what', 'which', 'winner',
                 'wins', 'win', 'to', 'be', 'in', 'of', 'for', 'or'}
    words = [w for w in t.split() if w not in stopwords]
    return ' '.join(words)


def group_kalshi_events(raw_markets):
    """将 Kalshi 市场按 event_ticker 分组，形成多结果事件列表。

    每个结果事件的子市场都在同一个 event_ticker 下，例如：
    event_ticker="PRES-2024" 包含 Trump/Harris/other 三个结果。

    Returns:
        [{'event_key', 'event_ticker', 'event_title', 'event_title_norm',
          'event_url', 'platform', 'outcomes': [...]}]
    """
    from collections import defaultdict
    groups = defaultdict(list)
    for m in raw_markets:
        et = m.get('event_ticker', '')
        if et:
            groups[et].append(m)

    now_utc = datetime.now(timezone.utc)
    events = []

    for event_ticker, markets in groups.items():
        outcomes = []
        for m in markets:
            # Skip expired markets
            close_time = m.get('close_time', '')
            if close_time:
                try:
                    ct = datetime.fromisoformat(close_time.replace('Z', '+00:00'))
                    if ct < now_utc:
                        continue
                except Exception:
                    pass

            yes_ask_str = m.get('yes_ask_dollars', '0') or '0'
            try:
                price = float(yes_ask_str)
            except (ValueError, TypeError):
                continue
            if price <= 0 or price >= 1:
                continue

            # Kalshi markets: 'title' is the full question or outcome label,
            # 'subtitle' may be the shorter outcome label (1-4 words)
            raw_title = m.get('title', '').strip()
            subtitle = m.get('subtitle', '').strip()

            # Prefer subtitle when it looks like a short outcome label
            if subtitle and 1 <= len(subtitle.split()) <= 4:
                label = subtitle
            elif raw_title:
                label = _extract_outcome_label(raw_title)
            else:
                label = m.get('ticker', event_ticker)

            outcomes.append({
                'label': label,
                'label_norm': label.lower().strip(),
                'price': round(price, 4),
                'url': f"https://kalshi.com/markets/{event_ticker}",
                'platform': 'Kalshi',
                'platform_color': '#3fb950',
            })

        if len(outcomes) < 3:
            continue

        # Derive event title: remove outcome label from first market's title
        first_title = markets[0].get('title', '') if markets else ''
        first_label = outcomes[0]['label'] if outcomes else ''
        if first_title and first_label:
            import re as _re
            event_title = _re.sub(
                r'(?i)^' + _re.escape(first_label) + r'\s*(to\s+\w+\s+)?',
                '', first_title
            ).strip(' ,.;')
            if len(event_title) < 5:
                event_title = event_ticker.replace('-', ' ')
        else:
            event_title = event_ticker.replace('-', ' ')

        events.append({
            'event_key': f'kal-{event_ticker}',
            'event_ticker': event_ticker,
            'event_title': event_title,
            'event_title_norm': _normalize_title_for_matching(event_title),
            'event_url': f"https://kalshi.com/markets/{event_ticker}",
            'platform': 'Kalshi',
            'outcomes': outcomes,
        })

    logger.debug(f"Kalshi event groups: {len(events)} events with 3+ outcomes")
    return events


def group_predict_events(raw_markets, orderbooks):
    """将 Predict.fun 子市场按推导的父 slug 分组，形成多结果事件列表。"""
    from collections import defaultdict
    groups = defaultdict(list)

    for m in raw_markets:
        market_id = m.get('id', m.get('market_id', ''))
        ob = orderbooks.get(market_id)
        if not ob:
            continue
        yes_ask = ob.get('yes_ask')
        if yes_ask is None or yes_ask <= 0 or yes_ask >= 1:
            continue

        question_text = m.get('question') or m.get('title', '')
        parent_slug = (
            m.get('groupSlug') or m.get('parentSlug') or
            m.get('eventSlug') or m.get('marketSlug') or
            m.get('market_slug') or m.get('group_slug') or
            m.get('parent_slug')
        )
        if not parent_slug:
            parent_slug = question_to_predict_slug(question_text)

        # Individual outcome slug (e.g., "england", "brazil")
        outcome_slug = m.get('slug', '')

        groups[parent_slug].append({
            'market_id': market_id,
            'question': question_text,
            'outcome_slug': outcome_slug,
            'yes_ask': yes_ask,
            'parent_slug': parent_slug,
        })

    events = []
    for parent_slug, sub_markets in groups.items():
        if len(sub_markets) < 3:
            continue

        outcomes = []
        for sm in sub_markets:
            # Use slugified outcome as the label (e.g., "england" → "England")
            label = sm['outcome_slug'].replace('-', ' ').strip() if sm['outcome_slug'] else ''
            if not label or len(label) < 2:
                label = _extract_outcome_label(sm['question'])

            outcomes.append({
                'label': label.title() if label.islower() else label,
                'label_norm': label.lower().strip(),
                'price': round(sm['yes_ask'], 4),
                'url': f"https://predict.fun/market/{parent_slug}",
                'platform': 'Predict',
                'platform_color': '#9c27b0',
            })

        # Event title from parent_slug
        event_title = parent_slug.replace('-', ' ').title()

        events.append({
            'event_key': f'pre-{parent_slug}',
            'event_slug': parent_slug,
            'event_title': event_title,
            'event_title_norm': _normalize_title_for_matching(parent_slug),
            'event_url': f"https://predict.fun/market/{parent_slug}",
            'platform': 'Predict',
            'outcomes': outcomes,
        })

    logger.debug(f"Predict event groups: {len(events)} events with 3+ outcomes")
    return events


def group_polymarket_events_for_combo(poly_events):
    """将 Polymarket 事件缓存格式化为跨平台组单所需结构。"""
    events = []
    now_utc = datetime.now(timezone.utc)

    for event in poly_events:
        sub_markets = event.get('markets', [])
        if len(sub_markets) < 3:
            continue

        event_id = event.get('id', '')
        event_slug = event.get('slug', str(event_id))
        event_title = event.get('title', event_slug)
        event_url = f"https://polymarket.com/event/{event_slug}"

        outcomes = []
        for m in sub_markets:
            # 过滤已结算的子市场
            if m.get('closed') is True or m.get('active') is False:
                continue

            end_date_str = m.get('endDate') or m.get('endDateIso', '')
            if end_date_str:
                try:
                    end_dt = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                    if end_dt < now_utc:
                        continue
                except Exception:
                    pass

            best_ask = m.get('bestAsk')
            yes_price = None
            if best_ask is not None:
                try:
                    yes_price = float(best_ask)
                except (ValueError, TypeError):
                    pass
            if yes_price is None or yes_price <= 0:
                outcome_str = m.get('outcomePrices', '[]')
                try:
                    prices = json.loads(outcome_str) if isinstance(outcome_str, str) else outcome_str
                    if prices:
                        yes_price = float(prices[0])
                except Exception:
                    pass

            if yes_price is None or yes_price <= 0 or yes_price >= 1:
                continue

            question = m.get('question', '')
            label = _extract_outcome_label(question) or question[:30]

            outcomes.append({
                'label': label,
                'label_norm': label.lower().strip(),
                'price': round(yes_price, 4),
                'url': event_url,
                'platform': 'Polymarket',
                'platform_color': '#03a9f4',
            })

        if len(outcomes) < 3:
            continue

        events.append({
            'event_key': f'poly-{event_id}',
            'event_title': event_title,
            'event_title_norm': _normalize_title_for_matching(event_title),
            'event_url': event_url,
            'platform': 'Polymarket',
            'outcomes': outcomes,
        })

    return events


def group_probable_events(probable_markets):
    """将 Probable Markets 市场按事件分组，形成多结果事件列表。

    Args:
        probable_markets: 已解析的市场列表（来自 fetch_probable_data，包含 yes/no 价格）

    Returns:
        [{'event_key', 'event_title', 'event_title_norm', 'event_url',
          'platform', 'outcomes': [...]}]
    """
    from collections import defaultdict

    # 按事件标题分组（从 URL 中提取事件标识）
    event_groups = defaultdict(list)

    for market in probable_markets:
        # 从 URL 提取事件标识
        url = market.get('url', '')
        yes_price = market.get('yes', 0)
        no_price = market.get('no', 0)

        # 过滤无效价格
        if yes_price <= 0 or yes_price >= 1:
            continue

        # 从标题中提取结果标签
        title = strip_html(market.get('title', ''))
        label = _extract_outcome_label(title) or title[:40]

        # 使用 URL 的事件部分作为事件分组键
        # URL 格式: https://probable.markets/event/event-slug?market=xxx
        event_slug = url.split('/event/')[-1].split('?')[0] if '/event/' in url else 'unknown'
        event_url = url.split('?')[0] if '?' in url else url

        event_groups[event_slug].append({
            'label': label,
            'label_norm': label.lower().strip(),
            'price': round(yes_price, 4),
            'url': url,
            'platform': 'Probable',
        })

    # 构建结果
    result = []
    for event_slug, outcomes_list in event_groups.items():
        if len(outcomes_list) < 3:
            continue

        # 使用第一个结果的标题作为事件标题
        first_title = outcomes_list[0].get('label', '')
        event_title = f"Probable: {event_slug}"

        result.append({
            'event_key': f'prob-{event_slug}',
            'event_title': event_title,
            'event_title_norm': _normalize_title_for_matching(event_title),
            'event_url': event_url,
            'platform': 'Probable',
            'outcomes': outcomes_list,
        })

    logger.info(f"Probable event groups: {len(result)} events with 3+ outcomes")
    return result


def find_cross_platform_multi_outcome_arb(
        kalshi_raw, predict_raw, predict_obs, poly_events,
        opinion_markets, probable_markets, threshold=0.5):
    """检测跨平台多结果组单套利机会。

    对同一个事件（如2026世界杯冠军），在不同平台为每个结果选择最低价格：
      - England 在 Kalshi 最便宜 → 在 Kalshi 买 England
      - Brazil 在 Predict 最便宜 → 在 Predict 买 Brazil
      - France 在 Polymarket 最便宜 → 在 Polymarket 买 France

    如果所有结果总成本 < $1，则存在无风险套利。
    仅当至少 2 个不同平台的结果形成组合时才计入（纯同平台机会已由
    find_polymarket_multi_outcome_arbitrage 处理）。

    Args:
        kalshi_raw:    Kalshi 原始市场列表
        predict_raw:   Predict.fun 原始市场列表
        predict_obs:   Predict.fun 订单簿缓存 {market_id: ob_dict}
        poly_events:   Polymarket 事件缓存
        opinion_markets: Opinion 已解析市场列表（用于补充单个结果的更低价格）
        probable_markets: Probable Markets 已解析市场列表
        threshold:     最低毛利率阈值（%）

    Returns:
        套利机会列表（与 find_polymarket_multi_outcome_arbitrage 格式兼容）
    """
    from difflib import SequenceMatcher

    try:
        kalshi_events = group_kalshi_events(kalshi_raw) if kalshi_raw else []
    except Exception as e:
        logger.warning(f"Cross-combo: Kalshi grouping error: {e}")
        kalshi_events = []
    try:
        predict_events = group_predict_events(predict_raw, predict_obs) if predict_raw else []
    except Exception as e:
        logger.warning(f"Cross-combo: Predict grouping error: {e}")
        predict_events = []
    try:
        poly_event_groups = group_polymarket_events_for_combo(poly_events) if poly_events else []
    except Exception as e:
        logger.warning(f"Cross-combo: Polymarket grouping error: {e}")
        poly_event_groups = []
    try:
        probable_events = group_probable_events(probable_markets) if probable_markets else []
    except Exception as e:
        logger.warning(f"Cross-combo: Probable grouping error: {e}")
        probable_events = []

    logger.info(
        f"Cross-combo groups: Kalshi={len(kalshi_events)}, "
        f"Predict={len(predict_events)}, Poly={len(poly_event_groups)}, Probable={len(probable_events)}"
    )

    if len(kalshi_events) + len(predict_events) + len(poly_event_groups) + len(probable_events) < 2:
        return []

    # Build Opinion lookup for supplementary single-outcome matching:
    # {normalized_title: {yes_price, url, platform}}
    opinion_lookup = {}
    for om in (opinion_markets or []):
        raw_title = strip_html(om.get('title', ''))
        norm = _normalize_title_for_matching(raw_title)
        opinion_lookup[norm] = {
            'yes_price': om.get('yes', 0),
            'url': om.get('url', ''),
            'raw_title': raw_title,
        }

    # Build Probable lookup for supplementary single-outcome matching:
    probable_lookup = {}
    for pm in (probable_markets or []):
        raw_title = strip_html(pm.get('title', ''))
        norm = _normalize_title_for_matching(raw_title)
        probable_lookup[norm] = {
            'yes_price': pm.get('yes', 0),
            'url': pm.get('url', ''),
            'raw_title': raw_title,
        }

    all_platform_groups = [
        ('Kalshi', kalshi_events),
        ('Predict', predict_events),
        ('Polymarket', poly_event_groups),
        ('Probable', probable_events),
    ]

    # Pairwise event matching across platforms, then build merged outcome map
    processed_combos = set()
    opportunities = []
    now_str = datetime.now(_TZ_CST).strftime('%H:%M:%S')
    fee_rate = 0.02  # conservative 2% per leg

    for i, (plat_a, events_a) in enumerate(all_platform_groups):
        for j, (plat_b, events_b) in enumerate(all_platform_groups):
            if j <= i or not events_a or not events_b:
                continue

            for ea in events_a:
                title_a = ea['event_title_norm']
                best_score = 0.0
                best_eb = None

                for eb in events_b:
                    score = SequenceMatcher(None, title_a, eb['event_title_norm']).ratio()
                    if score > best_score:
                        best_score = score
                        best_eb = eb

                if best_score < 0.45 or best_eb is None:
                    continue

                combo_key = tuple(sorted([ea['event_key'], best_eb['event_key']]))
                if combo_key in processed_combos:
                    continue
                processed_combos.add(combo_key)

                # Build merged outcome map:
                # {outcome_label_norm: {platform_name: outcome_dict}}
                outcome_map = {}
                for o in ea['outcomes']:
                    ln = o['label_norm']
                    if ln not in outcome_map:
                        outcome_map[ln] = {}
                    outcome_map[ln][ea['platform']] = o

                # Match each outcome from best_eb into the map
                for o_b in best_eb['outcomes']:
                    lb = o_b['label_norm']
                    best_la = None
                    best_la_score = 0.0
                    for la in outcome_map:
                        if la == lb:
                            best_la_score = 1.0
                            best_la = la
                            break
                        score = (0.85 if (la in lb or lb in la)
                                 else SequenceMatcher(None, la, lb).ratio())
                        if score > best_la_score:
                            best_la_score = score
                            best_la = la

                    if best_la_score >= 0.55 and best_la is not None:
                        if best_eb['platform'] not in outcome_map[best_la]:
                            outcome_map[best_la][best_eb['platform']] = o_b
                    else:
                        # Unmatched outcome — add as new entry
                        outcome_map[lb] = {best_eb['platform']: o_b}

                # For each outcome, also check Opinion for a potentially lower price
                for ln, plat_opts in list(outcome_map.items()):
                    # Try to find a matching Opinion market for this outcome
                    outcome_label = next(iter(plat_opts.values()))['label']
                    event_title_words = set(ea['event_title_norm'].split())
                    for op_norm, op_info in opinion_lookup.items():
                        op_words = set(op_norm.split())
                        # Must share event context AND contain the outcome label
                        label_words = set(outcome_label.lower().split())
                        if (len(event_title_words & op_words) >= 2
                                and label_words.issubset(op_words)
                                and op_info['yes_price'] > 0):
                            opinion_outcome = {
                                'label': outcome_label,
                                'label_norm': ln,
                                'price': op_info['yes_price'],
                                'url': op_info['url'],
                                'platform': 'Opinion',
                                'platform_color': '#d29922',
                            }
                            if 'Opinion' not in plat_opts:
                                plat_opts['Opinion'] = opinion_outcome
                            break

                    # Try to find a matching Probable market for this outcome
                    for prob_norm, prob_info in probable_lookup.items():
                        prob_words = set(prob_norm.split())
                        label_words = set(outcome_label.lower().split())
                        if (len(event_title_words & prob_words) >= 2
                                and label_words.issubset(prob_words)
                                and prob_info['yes_price'] > 0):
                            probable_outcome = {
                                'label': outcome_label,
                                'label_norm': ln,
                                'price': prob_info['yes_price'],
                                'url': prob_info['url'],
                                'platform': 'Probable',
                                'platform_color': '#6366f1',
                            }
                            if 'Probable' not in plat_opts:
                                plat_opts['Probable'] = probable_outcome
                            break

                # Build optimal portfolio: pick cheapest platform per outcome
                portfolio = []
                for label_norm, plat_opts in outcome_map.items():
                    cheapest_plat, cheapest_outcome = min(
                        plat_opts.items(), key=lambda x: x[1]['price']
                    )
                    portfolio.append({
                        'name': cheapest_outcome['label'],
                        'price': cheapest_outcome['price'],
                        'url': cheapest_outcome['url'],
                        'platform': cheapest_plat,
                        'platform_color': cheapest_outcome.get('platform_color', '#888'),
                    })

                if len(portfolio) < 3:
                    continue

                total_cost = sum(o['price'] for o in portfolio)

                # MECE sanity check
                if total_cost < MULTI_OUTCOME_MIN_TOTAL_COST:
                    continue
                if total_cost >= 0.98:
                    continue  # 扣除手续费后无利润（98c + 2% fee ≈ 100c）

                gross_pct = (1.0 - total_cost) * 100
                if gross_pct < threshold:
                    continue

                # Fee per leg: use each platform's rate
                fee_cost = sum(
                    o['price'] * PLATFORM_FEES.get(o['platform'].lower(), fee_rate)
                    for o in portfolio
                ) * 100
                net_pct = gross_pct - fee_cost

                platforms_used = sorted(set(o['platform'] for o in portfolio))
                market_key = f"COMBO-{ea['event_key']}-{best_eb['event_key']}"

                # 计算 shares：跨平台组合套利受限于最小 ask_size（需同时购买所有结果）
                ask_sizes = [o.get('ask_size', 0) or 0 for o in portfolio]
                valid_sizes = [s for s in ask_sizes if s > 0]
                shares = min(valid_sizes) if valid_sizes else 0

                opportunities.append({
                    'event_title': ea['event_title'],
                    'event_url': ea['event_url'],
                    'platform': '+'.join(platforms_used),
                    'platform_color': '#ff9800',
                    'platforms': platforms_used,
                    'match_score': round(best_score, 2),
                    'outcomes': portfolio,
                    'outcome_count': len(portfolio),
                    'total_cost': round(total_cost * 100, 2),
                    'arbitrage': round(gross_pct, 2),
                    'shares': int(shares) if shares > 0 else None,  # 可购买的套利组合数（受限于最小 ask_size）
                    'net_profit': round(net_pct, 2),
                    'timestamp': now_str,
                    'market_key': market_key,
                    '_created_at': time.time(),
                    'arb_type': 'cross_combo',
                })

    opportunities.sort(key=lambda x: x['arbitrage'], reverse=True)
    logger.info(f"Cross-combo: {len(opportunities)} cross-platform multi-outcome opportunities found")
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
                'timestamp': datetime.now(_TZ_CST).strftime('%H:%M:%S'),
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
                'timestamp': datetime.now(_TZ_CST).strftime('%H:%M:%S'),
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

    IDLE_SCAN_MULTIPLIER = 4  # Scan 4x slower when no clients connected

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

            # === CONCURRENT FETCHING: all 5 platforms in parallel ===
            poly_status, poly_markets = 'unknown', []
            opinion_status, opinion_markets = 'unknown', []
            predict_status, predict_markets = 'unknown', []
            kalshi_status, kalshi_markets = 'unknown', []
            probable_status, probable_markets = 'unknown', []

            with ThreadPoolExecutor(max_workers=5, thread_name_prefix='fetch') as executor:
                futures = {
                    executor.submit(fetch_polymarket_data, config): 'polymarket',
                    executor.submit(fetch_opinion_data, config): 'opinion',
                    executor.submit(fetch_predict_data, config): 'predict',
                    executor.submit(fetch_kalshi_data, config): 'kalshi',
                    executor.submit(fetch_probable_data, config): 'probable',
                }

                for future in as_completed(futures, timeout=300):
                    platform = futures[future]
                    try:
                        status, markets = future.result(timeout=300)
                        if platform == 'polymarket':
                            poly_status, poly_markets = status, markets
                        elif platform == 'opinion':
                            opinion_status, opinion_markets = status, markets
                        elif platform == 'predict':
                            predict_status, predict_markets = status, markets
                        elif platform == 'kalshi':
                            kalshi_status, kalshi_markets = status, markets
                        elif platform == 'probable':
                            probable_status, probable_markets = status, markets

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
            # Probable Markets arbitrage disabled - price data unavailable via public API
            if not PROBABLE_ARBITRAGE_ENABLED:
                logger.info("[Probable] 套利计算已禁用 - 公共API不提供价格数据")
            else:
                platform_market_pairs.append(('Probable', probable_markets))

            for pname, pmarkets in platform_market_pairs:
                if pmarkets:
                    same_arb = find_same_platform_arbitrage(pmarkets, pname, threshold=0.5)
                    all_arb.extend(same_arb)

            # === Cross-platform arbitrage (all pairs of 5 platforms) ===
            cross_pairs_checked = 0
            cross_platform_combos = [
                (poly_markets, opinion_markets, 'Polymarket', 'Opinion'),
                (poly_markets, predict_markets, 'Polymarket', 'Predict'),
                (poly_markets, kalshi_markets, 'Polymarket', 'Kalshi'),
                (opinion_markets, predict_markets, 'Opinion', 'Predict'),
                (opinion_markets, kalshi_markets, 'Opinion', 'Kalshi'),
                (predict_markets, kalshi_markets, 'Predict', 'Kalshi'),
            ]
            # Probable Markets arbitrage disabled - price data unavailable via public API
            if PROBABLE_ARBITRAGE_ENABLED:
                cross_platform_combos.extend([
                    (poly_markets, probable_markets, 'Polymarket', 'Probable'),
                    (opinion_markets, probable_markets, 'Opinion', 'Probable'),
                    (predict_markets, probable_markets, 'Predict', 'Probable'),
                    (kalshi_markets, probable_markets, 'Kalshi', 'Probable'),
                ])
            for markets_a, markets_b, name_a, name_b in cross_platform_combos:
                if markets_a and markets_b:
                    arb = find_cross_platform_arbitrage(
                        markets_a, markets_b, name_a, name_b, threshold)
                    all_arb.extend(arb)
                    cross_pairs_checked += 1

            all_arb.sort(key=lambda x: x['arbitrage'], reverse=True)

            # === 手动屏蔽已知错误匹配 ===
            # 过滤掉人名不匹配的套利对（如 Eric Trump 被匹配到 Donald Trump）
            def is_bad_match(arb):
                market = arb.get('market', '').lower()
                # 检查是否包含不同的特朗普名字
                has_eric = 'eric trump' in market
                has_donald = 'donald trump' in market
                has_trump = 'trump' in market
                # 如果同时包含 Eric 和 Donald，说明是错误匹配
                # 或者如果标题包含 Eric Trump 但没有 Donald，却被匹配到了 Donald Trump 市场
                if has_eric and has_donald:
                    return True
                # 如果是 Eric Trump 但 direction 显示是 Polymarket vs Probable 且另一方是 Donald
                direction = arb.get('direction', '').lower()
                if 'eric' in market and 'trump' in market:
                    # 检查另一方是否是 donald trump
                    if 'donald' in direction or 'probable' in direction:
                        return True
                return False

            all_arb = [arb for arb in all_arb if not is_bad_match(arb)]

            # === Logical Spread Arbitrage (同平台逻辑价差套利) ===
            # 检测 Polymarket 上的价格阈值型和时间窗口型套利
            # 使用 /events 端点，在同一事件的子市场之间比较，避免跨事件错误匹配
            logical_spread_arb = []
            if LSA_ENABLED and _poly_events_cache:
                # 基于事件的版本：直接使用 events 数据
                logical_spread_arb = find_logical_spread_arbitrage(
                    _poly_events_cache, 'Polymarket', threshold=0.0)

            # === Multi-outcome arbitrage (Polymarket events with 3+ outcomes, same platform) ===
            # Uses _poly_events_cache populated by fetch_polymarket_data via /events endpoint.
            # Falls back to empty list if events fetch failed.
            multi_outcome_arb = find_polymarket_multi_outcome_arbitrage(_poly_events_cache, threshold=0.5)

            # === Cross-platform multi-outcome combo (Kalshi + Predict + Polymarket + Opinion + Probable) ===
            # For the same event, pick cheapest platform per outcome to build a complete portfolio.
            # Probable Markets arbitrage disabled - price data unavailable via public API
            probable_for_arb = probable_markets if PROBABLE_ARBITRAGE_ENABLED else []
            cross_combo_arb = find_cross_platform_multi_outcome_arb(
                _kalshi_raw_cache, _predict_raw_cache, _predict_ob_cache,
                _poly_events_cache, opinion_markets, probable_for_arb, threshold=0.5)

            # Merge same-platform and cross-platform multi-outcome into one list
            all_multi_arb = multi_outcome_arb + cross_combo_arb

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
                        if price_change < 0.1:
                            old_opp['timestamp'] = datetime.now(_TZ_CST).strftime('%H:%M:%S')
                            new_arb_map[key] = old_opp

                # Sort and limit
                sorted_arb = sorted(new_arb_map.values(), key=lambda x: x['arbitrage'], reverse=True)
                _state['arbitrage'] = sorted_arb[:MAX_ARBITRAGE_DISPLAY]

                # Store logical spread arb
                existing_lsa = _state.get('logical_spread_arb', [])
                new_lsa_map = {opp['market_key']: opp for opp in logical_spread_arb if opp.get('market_key')}
                old_lsa_map = {opp['market_key']: opp for opp in existing_lsa if opp.get('market_key')}
                for key, old_opp in old_lsa_map.items():
                    if key not in new_lsa_map:
                        if old_opp.get('_created_at', 0) > expiry_cutoff:
                            new_lsa_map[key] = old_opp
                sorted_lsa = sorted(new_lsa_map.values(), key=lambda x: x['arbitrage'], reverse=True)
                _state['logical_spread_arb'] = sorted_lsa[:MAX_ARBITRAGE_DISPLAY]

                # Merge multi-outcome arb (same-platform + cross-combo) with existing
                existing_multi = _state.get('multi_outcome_arb', [])
                new_multi_map = {opp['market_key']: opp for opp in all_multi_arb if opp.get('market_key')}
                old_multi_map = {opp['market_key']: opp for opp in existing_multi if opp.get('market_key')}
                for key, old_opp in old_multi_map.items():
                    if key not in new_multi_map:
                        if old_opp.get('_created_at', 0) > expiry_cutoff:
                            new_multi_map[key] = old_opp
                sorted_multi = sorted(new_multi_map.values(), key=lambda x: x['arbitrage'], reverse=True)
                _state['multi_outcome_arb'] = sorted_multi[:MAX_ARBITRAGE_DISPLAY]

                _state['scan_count'] += 1
                _state['last_scan'] = datetime.now(_TZ_CST).strftime('%Y-%m-%d %H:%M:%S')
                _state['threshold'] = threshold
                _state['error'] = None

                # Scan statistics for UI
                scan_duration = time.time() - scan_start
                same_count = sum(1 for a in all_arb if a.get('arb_type') == 'same_platform')
                cross_count = sum(1 for a in all_arb if a.get('arb_type') == 'cross_platform')
                profitable = sum(1 for a in all_arb if a.get('net_profit', 0) > 0)
                _state['scan_stats'] = {
                    'duration': round(scan_duration, 1),
                    'total_markets': len(poly_markets) + len(opinion_markets) + len(predict_markets) + len(kalshi_markets) + len(probable_markets),
                    'poly_count': len(poly_markets),
                    'opinion_count': len(opinion_markets),
                    'predict_count': len(predict_markets),
                    'kalshi_count': len(kalshi_markets),
                    'probable_count': len(probable_markets),
                    'cross_pairs_checked': cross_pairs_checked,
                    'same_platform_arb': same_count,
                    'cross_platform_arb': cross_count,
                    'profitable_after_fees': profitable,
                    'total_arb': len(all_arb),
                    'logical_spread_arb': len(logical_spread_arb),
                    'multi_outcome_arb': len(multi_outcome_arb),
                    'cross_combo_arb': len(cross_combo_arb),
                    'ws_updates': _ws_update_count,
                }

                # Update price history for active arb opportunities
                update_price_history(sorted_arb[:MAX_ARBITRAGE_DISPLAY])

            logger.info(
                f"Scan #{_state['scan_count']} ({scan_duration:.1f}s): "
                f"Poly={len(poly_markets)} Opinion={len(opinion_markets)} "
                f"Predict={len(predict_markets)} Kalshi={len(kalshi_markets)} "
                f"Probable={len(probable_markets)} "
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

        # Idle mode: sleep longer when no WebSocket clients, but check frequently
        # so we wake up quickly when someone connects
        if _ws_clients == 0:
            wait_total = scan_interval * IDLE_SCAN_MULTIPLIER
            waited = 0
            while waited < wait_total and _ws_clients == 0:
                time.sleep(5)
                waited += 5
        else:
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
