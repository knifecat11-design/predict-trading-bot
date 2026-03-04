"""
Cross-platform prediction market arbitrage monitor
Platforms: Polymarket, Opinion.trade, Predict.fun, Probable.markets, Kalshi
"""

import os
import sys
import json
import time
import logging
import threading
import traceback
from datetime import datetime, timedelta

# UTF-8 encoding
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def setup_logging(level=logging.INFO):
    handlers = [logging.StreamHandler(sys.stdout)]
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=handlers
    )
    return logging.getLogger(__name__)


def load_config():
    try:
        from src.config_helper import load_config as load_env_config
        return load_env_config()
    except ImportError:
        import yaml
        config_path = 'config.yaml'
        if not os.path.exists(config_path):
            return {
                'arbitrage': {
                    'min_arbitrage_threshold': float(os.getenv('MIN_ARBITRAGE_THRESHOLD', 2.0)),
                    'scan_interval': int(os.getenv('SCAN_INTERVAL', 30)),
                    'cooldown_minutes': int(os.getenv('COOLDOWN_MINUTES', 10))
                },
                'opinion': {
                    'api_key': os.getenv('OPINION_API_KEY', ''),
                    'base_url': 'https://proxy.opinion.trade:8443/openapi',
                },
                'opinion_poly': {
                    'min_arbitrage_threshold': float(os.getenv('OPINION_POLY_THRESHOLD', 2.0)),
                    'min_confidence': 0.2,
                },
                'probable': {
                    'enabled': os.getenv('PROBABLE_ENABLED', 'true').lower() == 'true',
                    'base_url': 'https://market-api.probable.markets/public/api/v1',
                },
                'notification': {
                    'telegram': {
                        'enabled': True,
                        'bot_token': os.getenv('TELEGRAM_BOT_TOKEN', ''),
                        'chat_id': os.getenv('TELEGRAM_CHAT_ID', '')
                    }
                },
                'logging': {'level': os.getenv('LOG_LEVEL', 'INFO')}
            }
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)


# ============================================================
# Telegram (with rate limit handling)
# ============================================================

_telegram_rate_limited_until = 0

def send_telegram(message, config):
    global _telegram_rate_limited_until

    try:
        if time.time() < _telegram_rate_limited_until:
            return False

        token = config.get('notification', {}).get('telegram', {}).get('bot_token')
        chat_id = config.get('notification', {}).get('telegram', {}).get('chat_id')
        if not token or not chat_id:
            logging.warning(f"Telegram credentials missing (token: {'set' if token else 'EMPTY'}, chat_id: {'set' if chat_id else 'EMPTY'})")
            return False

        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'},
            timeout=10
        )

        if resp.status_code == 200:
            return True
        elif resp.status_code == 429:
            retry_after = resp.json().get('parameters', {}).get('retry_after', 60)
            _telegram_rate_limited_until = time.time() + retry_after
            logging.warning(f"Telegram rate limited, retry after {retry_after}s")
            return False
        else:
            logging.error(f"Telegram error: {resp.status_code}")
            return False
    except Exception as e:
        logging.error(f"Telegram send failed: {e}")
        return False


# ============================================================
# Platform API status detection
# ============================================================

def check_platform_api(config):
    """Check which platform APIs are real (not mock)"""
    status = {
        'polymarket': True,  # Public API, always available
        'opinion': bool(config.get('opinion', {}).get('api_key', '')),
        'predict': False,
        'kalshi': True,  # Public API, always available
        'probable': config.get('probable', {}).get('enabled', True),  # Public API
    }

    # Check Predict.fun API (v1 API: x-api-key header, /v1/ prefix)
    predict_key = config.get('api', {}).get('api_key', '')
    if predict_key:
        try:
            import requests
            base_url = config.get('api', {}).get('base_url', 'https://api.predict.fun')
            logging.info(f"Predict API check: {base_url}/v1/markets (key: {predict_key[:8]}...)")
            resp = requests.get(
                f"{base_url}/v1/markets",
                headers={'x-api-key': predict_key},
                params={'first': 1, 'status': 'OPEN'},
                timeout=10
            )
            if resp.status_code == 200:
                status['predict'] = True
                logging.info("Predict API check: OK (200)")
            else:
                logging.warning(f"Predict API check: HTTP {resp.status_code} - {resp.text[:200]}")
        except requests.RequestException as e:
            logging.warning(f"Predict API check failed (network): {e}")
        except Exception as e:
            logging.warning(f"Predict API check failed: {e}")
    else:
        logging.warning("Predict API: no API key configured")

    # Check Probable Markets API
    if status['probable']:
        try:
            import requests
            base_url = config.get('probable', {}).get('base_url', 'https://market-api.probable.markets/public/api/v1')
            logging.info(f"Probable API check: {base_url}/events")
            resp = requests.get(
                f"{base_url}/events",
                params={'limit': 1},
                timeout=10
            )
            if resp.status_code == 200:
                logging.info("Probable API check: OK (200)")
            else:
                logging.warning(f"Probable API check: HTTP {resp.status_code} - disabling")
                status['probable'] = False
        except requests.RequestException as e:
            logging.warning(f"Probable API check failed (network): {e}")
            status['probable'] = False
        except Exception as e:
            logging.warning(f"Probable API check failed: {e}")
            status['probable'] = False

    return status


# ============================================================
# Cross-platform arbitrage scanning
# ============================================================

def fetch_polymarket_markets(config):
    """Fetch Polymarket markets — reuse dashboard logic for best_ask pricing"""
    try:
        from web.dashboard import fetch_polymarket_data
        status, markets = fetch_polymarket_data(config)
        # Convert to monitor format (strip HTML, keep match_title)
        parsed = []
        import re
        for m in markets:
            title_html = m.get('title', '')
            title_plain = re.sub(r'<[^>]+>', '', title_html)
            parsed.append({
                'id': m.get('id', ''),
                'title': title_plain[:80],
                'match_title': m.get('match_title', title_plain),
                'yes': m['yes'], 'no': m['no'],
                'volume': m.get('volume', 0),
                'end_date': m.get('end_date', ''),
            })
        parsed.sort(key=lambda x: x['volume'], reverse=True)
        return parsed
    except Exception as e:
        logging.error(f"Polymarket fetch: {e}")
        return []


def fetch_opinion_markets(config):
    """Fetch Opinion markets — reuse dashboard logic for best_ask + concurrent"""
    try:
        from web.dashboard import fetch_opinion_data
        status, markets = fetch_opinion_data(config)
        import re
        parsed = []
        for m in markets:
            title_html = m.get('title', '')
            title_plain = re.sub(r'<[^>]+>', '', title_html)
            parsed.append({
                'id': m.get('id', ''),
                'title': title_plain[:80],
                'match_title': m.get('match_title', title_plain),
                'yes': m['yes'], 'no': m['no'],
                'volume': m.get('volume', 0),
                'end_date': m.get('end_date', ''),
            })
        return parsed
    except Exception as e:
        logging.error(f"Opinion fetch: {e}")
        return []


def fetch_predict_markets(config):
    """Fetch Predict.fun markets — reuse dashboard logic for full pagination + concurrent"""
    try:
        from web.dashboard import fetch_predict_data
        status, markets = fetch_predict_data(config)
        import re
        parsed = []
        for m in markets:
            title_html = m.get('title', '')
            title_plain = re.sub(r'<[^>]+>', '', title_html)
            parsed.append({
                'id': m.get('id', ''),
                'title': title_plain[:80],
                'match_title': m.get('match_title', title_plain),
                'yes': m['yes'], 'no': m['no'],
                'volume': m.get('volume', 0),
                'end_date': m.get('end_date', ''),
            })
        return parsed
    except Exception as e:
        logging.error(f"Predict fetch: {e}")
        return []


def fetch_kalshi_markets(config):
    """Fetch Kalshi markets — reuse dashboard logic"""
    try:
        from web.dashboard import fetch_kalshi_data
        status, markets = fetch_kalshi_data(config)
        import re
        parsed = []
        for m in markets:
            title_html = m.get('title', '')
            title_plain = re.sub(r'<[^>]+>', '', title_html)
            parsed.append({
                'id': m.get('id', ''),
                'title': title_plain[:80],
                'match_title': m.get('match_title', title_plain),
                'yes': m['yes'], 'no': m['no'],
                'volume': m.get('volume', 0),
                'end_date': m.get('end_date', ''),
            })
        return parsed
    except Exception as e:
        logging.error(f"Kalshi fetch: {e}")
        return []


def fetch_probable_markets(config):
    """Fetch Probable Markets — using probable_api client"""
    try:
        from src.probable_api import ProbableClient
        client = ProbableClient(config)
        return client.get_markets_for_arbitrage(limit=500)
    except Exception as e:
        logging.error(f"Probable fetch: {e}")
        return []



def find_arbitrage(markets_a, markets_b, name_a, name_b, threshold=2.0, min_confidence=0.2, excluded_markets=None):
    """Find cross-platform arbitrage using MarketMatcher (same algorithm as dashboard)

    Uses the sophisticated MarketMatcher from src/market_matcher.py with:
    - Weighted scoring: Entity (40%) + Number/date (30%) + Vocabulary (20%) + String (10%)
    - Manual mapping support
    - Hard constraints on year/price matching

    Args:
        markets_a, markets_b: Market lists from two platforms
        name_a, name_b: Platform names (e.g., 'Polymarket', 'Kalshi')
        threshold: Minimum arbitrage percentage to report
        min_confidence: Minimum matching confidence (unused, kept for compatibility)
        excluded_markets: Dict of platform -> list of excluded market IDs/slugs
    """
    try:
        from src.market_matcher import MarketMatcher
    except ImportError:
        logging.error("Cannot import MarketMatcher — cross-platform matching disabled")
        return []

    # Build excluded market sets for both platforms
    excluded_a = set()
    excluded_b = set()
    if excluded_markets:
        excluded_a = set(excluded_markets.get(name_a.lower(), []))
        excluded_b = set(excluded_markets.get(name_b.lower(), []))

    # Helper function to check if a market should be excluded
    def is_excluded(market, excluded_set):
        market_id = str(market.get('id', ''))
        market_slug = str(market.get('slug', ''))
        market_question_id = str(market.get('question_id', ''))
        market_condition_id = str(market.get('condition_id', ''))
        return (market_id in excluded_set or
                market_slug in excluded_set or
                market_question_id in excluded_set or
                market_condition_id in excluded_set)

    # Filter out excluded markets
    markets_a_filtered = [m for m in markets_a if not is_excluded(m, excluded_a)]
    markets_b_filtered = [m for m in markets_b if not is_excluded(m, excluded_b)]

    excluded_count_a = len(markets_a) - len(markets_a_filtered)
    excluded_count_b = len(markets_b) - len(markets_b_filtered)
    if excluded_count_a > 0 or excluded_count_b > 0:
        logging.info(f"  [Filter] Excluded {excluded_count_a} {name_a} markets, {excluded_count_b} {name_b} markets")

    # Prepare markets with match_title for the matcher
    markets_a_prepared = []
    for m in markets_a_filtered:
        m_copy = m.copy()
        m_copy['match_title'] = m.get('match_title', m.get('title', ''))
        markets_a_prepared.append(m_copy)

    markets_b_prepared = []
    for m in markets_b_filtered:
        m_copy = m.copy()
        m_copy['match_title'] = m.get('match_title', m.get('title', ''))
        markets_b_prepared.append(m_copy)

    matcher = MarketMatcher({})
    matched_pairs = matcher.match_markets_cross_platform(
        markets_a_prepared, markets_b_prepared,
        title_field_a='match_title', title_field_b='match_title',
        id_field_a='id', id_field_b='id',
        platform_a=name_a.lower(), platform_b=name_b.lower(),
        min_similarity=0.60,
    )

    logging.info(f"  [{name_a} vs {name_b}] MarketMatcher found {len(matched_pairs)} pairs")

    results = []
    for ma, mb, confidence in matched_pairs:
        # Price sanity: genuine matched markets must roughly agree on probability
        if abs(ma['yes'] - mb['yes']) > 0.40:
            continue

        comb1 = ma['yes'] + mb['no']
        arb1 = (1.0 - comb1) * 100

        comb2 = mb['yes'] + ma['no']
        arb2 = (1.0 - comb2) * 100

        market_key_base = f"{name_a}-{name_b}-{ma.get('id','')}-{mb.get('id','')}"

        for arb_pct, direction, market_title, ya, na, yb, nb, key_suffix in [
            (arb1, f"{name_a} Buy Yes + {name_b} Buy No", ma.get('title', ''),
             ma['yes'], ma['no'], mb['yes'], mb['no'], '-yes1_no2'),
            (arb2, f"{name_b} Buy Yes + {name_a} Buy No", mb.get('title', ''),
             mb['yes'], mb['no'], ma['yes'], ma['no'], '-yes2_no1'),
        ]:
            if arb_pct >= threshold:
                results.append({
                    'market': market_title,
                    'platforms': f"{name_a} <-> {name_b}",
                    'direction': direction,
                    'arbitrage': round(arb_pct, 2),
                    'a_yes': round(ya * 100, 2), 'a_no': round(na * 100, 2),
                    'b_yes': round(yb * 100, 2), 'b_no': round(nb * 100, 2),
                    'confidence': round(confidence, 2),
                    'market_key': market_key_base + key_suffix,
                })

    results.sort(key=lambda x: x['arbitrage'], reverse=True)
    return results


def find_same_platform_arb(markets, platform_name, threshold=0.5):
    """Detect same-platform arbitrage: Yes_ask + No_ask < $1.00"""
    results = []
    for m in markets:
        try:
            yes_ask = m.get('yes', 0)
            no_ask = m.get('no', 0)
            if not yes_ask or not no_ask or yes_ask <= 0 or no_ask <= 0:
                continue

            total_cost = yes_ask + no_ask
            if total_cost >= 1.0:
                continue

            gross_pct = (1.0 - total_cost) * 100
            if gross_pct < threshold:
                continue

            market_key = f"SAME-{platform_name}-{m.get('id', m.get('title', '')[:30])}"
            results.append({
                'market': m.get('title', ''),
                'platforms': f"{platform_name} (same platform)",
                'direction': f"{platform_name} Buy Yes + Buy No",
                'arbitrage': round(gross_pct, 2),
                'a_yes': round(yes_ask * 100, 2), 'a_no': round(no_ask * 100, 2),
                'b_yes': round(yes_ask * 100, 2), 'b_no': round(no_ask * 100, 2),
                'confidence': 1.0,
                'market_key': market_key,
                'is_real': True,
            })
        except Exception:
            continue

    if results:
        logging.info(f"  [{platform_name} SAME] Found {len(results)} same-platform arbitrage")
    return results


# ============================================================
# Logical Spread Arbitrage
# ============================================================

def scan_logical_spread_arbitrage(markets, platform_name, config, detector=None):
    """
    扫描逻辑价差套利机会

    检测同一平台内具有逻辑包含关系的事件对之间的价差套利
    """
    try:
        from src.logical_spread_arbitrage import LogicalSpreadArbitrageDetector
    except ImportError:
        logging.warning("Logical spread arbitrage module not available")
        return [], None

    if detector is None:
        detector = LogicalSpreadArbitrageDetector(config)

    # 构建价格字典
    price_dict = {m.get('id', ''): m.get('yes', 0) for m in markets if m.get('id')}

    # 执行扫描
    arbitrage_pairs = detector.scan_markets(markets, price_dict, platform_name)

    return arbitrage_pairs, detector


def format_logical_spread_message(pair, scan_count):
    """格式化逻辑价差套利通知消息"""
    spread_pct = pair.spread * 100
    profit_pct = pair.arbitrage_profit * 100
    hard_yes_pct = pair.hard_price * 100
    easy_yes_pct = pair.easy_price * 100

    type_name = {
        'price_threshold': '价格阈值',
        'time_window': '时间窗口',
        'conditional': '条件层级',
        'multi_outcome': '多结果分解',
    }.get(pair.logical_type.value, '未知类型')

    return (
        f"<b>🔗 逻辑价差套利 #{scan_count}</b>\n"
        f"\n"
        f"<b>类型:</b> {type_name}\n"
        f"<b>平台:</b> {pair.platform.title()}\n"
        f"\n"
        f"<b>逻辑关系:</b> {pair.relationship_desc}\n"
        f"\n"
        f"<b>较难事件 (Hard):</b>\n"
        f"  {pair.hard_title[:60]}...\n"
        f"  YES价格: {hard_yes_pct:.1f}%\n"
        f"\n"
        f"<b>较易事件 (Easy):</b>\n"
        f"  {pair.easy_title[:60]}...\n"
        f"  YES价格: {easy_yes_pct:.1f}%\n"
        f"\n"
        f"<b>价差:</b> {spread_pct:.2f}% (正常应为负)\n"
        f"<b>套利成本:</b> {pair.arbitrage_cost:.1f}%\n"
        f"<b>预期收益:</b> {profit_pct:.2f}%\n"
        f"\n"
        f"<b>策略:</b> 买入 Hard 的 NO + 买入 Easy 的 YES\n"
        f"\n"
        f"<b>时间:</b> {datetime.now().strftime('%H:%M:%S')}"
    )


# ============================================================
# Original Message Formatting
# ============================================================

def format_arb_message(opp, scan_count):
    """Format arbitrage as Telegram message (binary or multi-outcome)"""
    arb_type = opp.get('arb_type', '')

    # Check for logical spread arbitrage
    if opp.get('is_logical_spread'):
        return format_logical_spread_message(opp.get('pair_obj'), scan_count)

    # Multi-outcome arbitrage (same-platform or cross-platform combo)
    if arb_type in ('multi_outcome', 'cross_combo'):
        outcomes = opp.get('outcomes', [])
        # Show top outcomes (limit to 8 to avoid oversized messages)
        outcome_lines = []
        for o in sorted(outcomes, key=lambda x: x['price'], reverse=True)[:8]:
            plat_tag = f" [{o['platform']}]" if arb_type == 'cross_combo' else ""
            outcome_lines.append(f"  • {o['name']}: {o['price']*100:.1f}c{plat_tag}")
        if len(outcomes) > 8:
            outcome_lines.append(f"  ... +{len(outcomes)-8} more")
        outcomes_text = "\n".join(outcome_lines)

        type_label = "多结果套利" if arb_type == 'multi_outcome' else "跨平台组合套利"
        return (
            f"<b>🎰 {type_label} #{scan_count}</b>\n"
            f"<b>事件:</b> {opp['event_title']}\n"
            f"<b>平台:</b> {opp['platform']}\n"
            f"<b>结果数:</b> {opp['outcome_count']}\n"
            f"<b>总成本:</b> {opp['total_cost']:.1f}c\n"
            f"<b>套利空间:</b> {opp['arbitrage']:.2f}%\n\n"
            f"<b>各结果价格:</b>\n"
            f"{outcomes_text}\n\n"
            f"<b>时间:</b> {datetime.now().strftime('%H:%M:%S')}"
        )

    # Binary arbitrage (cross-platform or same-platform)
    return (
        f"<b>🎯 套利机会 #{scan_count}</b>\n"
        f"<b>市场:</b> {opp['market']}\n"
        f"<b>平台:</b> {opp['platforms']}\n"
        f"<b>方向:</b> {opp['direction']}\n"
        f"<b>套利空间:</b> {opp['arbitrage']:.2f}%\n\n"
        f"<b>Platform A:</b> Yes {opp['a_yes']}c  No {opp['a_no']}c\n"
        f"<b>Platform B:</b> Yes {opp['b_yes']}c  No {opp['b_no']}c\n"
        f"<b>置信度:</b> {opp['confidence']:.0%}\n"
        f"<b>时间:</b> {datetime.now().strftime('%H:%M:%S')}"
    )


# ============================================================
# Main
# ============================================================

def main():
    print()
    print("=" * 70)
    print("  Cross-Platform Arbitrage Monitor")
    print("  Polymarket | Opinion.trade | Predict.fun | Probable | Kalshi")
    print("  Version: v2.4 (2026-03-04) - 新增 UMA Oracle 争议信号检测")
    print("=" * 70)
    print()

    logger = setup_logging()
    config = load_config()

    # Config
    arb_config = config.get('arbitrage', {})
    op_config = config.get('opinion_poly', {})
    lsa_config = config.get('logical_spread_arbitrage', {})
    scan_interval = arb_config.get('scan_interval', 30)
    cooldown_minutes = arb_config.get('cooldown_minutes', 10)
    threshold = op_config.get('min_arbitrage_threshold', 2.0)
    min_confidence = op_config.get('min_confidence', 0.2)

    # 市场黑名单配置
    excluded_markets = arb_config.get('excluded_markets', {})
    if excluded_markets:
        logger.info(f"Excluded markets configured:")
        for platform, markets in excluded_markets.items():
            if markets:
                logger.info(f"  {platform}: {len(markets)} markets")

    # Logical Spread Arbitrage 配置
    lsa_enabled = lsa_config.get('enabled', True)
    lsa_min_spread = lsa_config.get('min_spread_threshold', 0.5)

    # Dispute Signal 配置
    dispute_config = config.get('dispute', {})
    dispute_enabled = os.getenv('DISPUTE_SIGNAL_ENABLED', str(dispute_config.get('enabled', True))).lower() == 'true'
    dispute_scan_interval = int(os.getenv('DISPUTE_SCAN_INTERVAL', dispute_config.get('scan_interval', 120)))
    dispute_cooldown_minutes = int(os.getenv('DISPUTE_COOLDOWN_MINUTES', dispute_config.get('cooldown_minutes', 30)))

    # Check API status
    api_status = check_platform_api(config)
    logger.info(f"Platform API Status:")
    logger.info(f"  Polymarket: {'Active' if api_status['polymarket'] else 'Inactive'}")
    logger.info(f"  Opinion:    {'Active' if api_status['opinion'] else 'No API Key'}")
    logger.info(f"  Predict:    {'Active' if api_status['predict'] else 'No API Key'}")
    logger.info(f"  Kalshi:     {'Active' if api_status['kalshi'] else 'Inactive'}")
    logger.info(f"  Probable:   {'Active' if api_status['probable'] else 'Inactive'}")
    logger.info(f"  UMA Oracle: {'Active' if dispute_enabled else 'Disabled'}")
    logger.info(f"Threshold: {threshold}%  Interval: {scan_interval}s  Cooldown: {cooldown_minutes}m")
    logger.info("")

    # 初始化争议信号检测器
    dispute_detector = None
    uma_client = None
    if dispute_enabled:
        try:
            from src.uma_oracle_api import UMAOracleClient
            from src.dispute_signal import DisputeSignalDetector, format_dispute_signal_message, Severity
            uma_client = UMAOracleClient(config)
            dispute_detector = DisputeSignalDetector(uma_client, config)
            logger.info("Dispute signal detector initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize dispute detector: {e}")
            dispute_enabled = False

    # Startup notification
    active_platforms = [k for k, v in api_status.items() if v]
    lsa_status = "ON" if lsa_enabled else "OFF"
    dispute_status = "ON" if dispute_enabled else "OFF"
    sent = send_telegram(
        f"🚀 <b>Arbitrage Monitor Started</b>\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Active: {', '.join(active_platforms)}\n"
        f"Threshold: {threshold}%\n"
        f"Logical Spread: {lsa_status}\n"
        f"Dispute Signal: {dispute_status}",
        config
    )
    logger.info(f"Startup notification: {'sent' if sent else 'failed (rate limited?)'}")

    # State
    running = True
    scan_count = 0
    last_notifications = {}
    last_sent_opportunities = {}  # 存储上次发送的机会详情
    lsa_detector = None  # Logical Spread Arbitrage 检测器
    last_dispute_scan = 0  # 上次争议扫描时间戳
    dispute_notifications = {}  # 争议信号冷却跟踪

    try:
        import signal
        def handler(sig, frame):
            nonlocal running
            logger.info("Stopping...")
            running = False
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
    except:
        pass

    logger.info("Starting scan loop...")

    try:
        while running:
            scan_count += 1
            logger.info(f"=[ Scan #{scan_count} ]=")

            # Fetch markets from active platforms
            poly_markets = fetch_polymarket_markets(config) if api_status['polymarket'] else []
            opinion_markets = fetch_opinion_markets(config) if api_status['opinion'] else []
            predict_markets = fetch_predict_markets(config) if api_status['predict'] else []
            kalshi_markets = fetch_kalshi_markets(config) if api_status['kalshi'] else []
            probable_markets = fetch_probable_markets(config) if api_status['probable'] else []

            logger.info(f"  Polymarket: {len(poly_markets)}  Opinion: {len(opinion_markets)}  Predict: {len(predict_markets)}  Kalshi: {len(kalshi_markets)}  Probable: {len(probable_markets)}")

            # === Same-platform arbitrage (Yes+No < $1.00) ===
            all_opps = []
            platform_market_pairs = [
                ('Polymarket', poly_markets, True),
                ('Opinion', opinion_markets, api_status['opinion']),
                ('Predict', predict_markets, api_status['predict']),
                ('Kalshi', kalshi_markets, True),
                ('Probable', probable_markets, api_status['probable']),
            ]
            for pname, pmarkets, is_real in platform_market_pairs:
                if pmarkets:
                    same_opps = find_same_platform_arb(pmarkets, pname, threshold=0.5)
                    for opp in same_opps:
                        opp['is_real'] = is_real
                    all_opps.extend(same_opps)

            # === Cross-platform arbitrage (all pairs) ===
            # 注：跨平台套利的数据来源与 dashboard 相同（public API），无需 is_real 过滤
            pairs = []
            if poly_markets and opinion_markets:
                pairs.append((poly_markets, opinion_markets, 'Polymarket', 'Opinion'))
            if poly_markets and predict_markets:
                pairs.append((poly_markets, predict_markets, 'Polymarket', 'Predict'))
            if poly_markets and kalshi_markets:
                pairs.append((poly_markets, kalshi_markets, 'Polymarket', 'Kalshi'))
            if poly_markets and probable_markets:
                pairs.append((poly_markets, probable_markets, 'Polymarket', 'Probable'))
            if opinion_markets and predict_markets:
                pairs.append((opinion_markets, predict_markets, 'Opinion', 'Predict'))
            if opinion_markets and kalshi_markets:
                pairs.append((opinion_markets, kalshi_markets, 'Opinion', 'Kalshi'))
            if opinion_markets and probable_markets:
                pairs.append((opinion_markets, probable_markets, 'Opinion', 'Probable'))
            if predict_markets and kalshi_markets:
                pairs.append((predict_markets, kalshi_markets, 'Predict', 'Kalshi'))
            if predict_markets and probable_markets:
                pairs.append((predict_markets, probable_markets, 'Predict', 'Probable'))
            if kalshi_markets and probable_markets:
                pairs.append((kalshi_markets, probable_markets, 'Kalshi', 'Probable'))

            for ma, mb, na, nb in pairs:
                opps = find_arbitrage(ma, mb, na, nb, threshold, min_confidence, excluded_markets)
                for opp in opps:
                    opp['is_real'] = True  # 跨平台套利数据来自 public API，无需过滤
                all_opps.extend(opps)

            # === Multi-outcome arbitrage (reuse dashboard functions + caches) ===
            multi_count = 0
            combo_count = 0
            lsa_count = 0  # Logical spread arbitrage count

            try:
                import web.dashboard as _dash
                from web.dashboard import (find_polymarket_multi_outcome_arbitrage,
                                           find_cross_platform_multi_outcome_arb)

                # Polymarket same-platform multi-outcome (3+ outcome events)
                multi_opps = find_polymarket_multi_outcome_arbitrage(
                    _dash._poly_events_cache, threshold=0.5)
                for opp in multi_opps:
                    opp['is_real'] = True
                all_opps.extend(multi_opps)
                multi_count = len(multi_opps)

                # Cross-platform multi-outcome combo (cheapest per outcome across platforms)
                cross_combo_opps = find_cross_platform_multi_outcome_arb(
                    _dash._kalshi_raw_cache, _dash._predict_raw_cache,
                    _dash._predict_ob_cache, _dash._poly_events_cache,
                    opinion_markets, threshold=0.5)
                for opp in cross_combo_opps:
                    opp['is_real'] = True
                all_opps.extend(cross_combo_opps)
                combo_count = len(cross_combo_opps)
            except Exception as e:
                logging.warning(f"  Multi-outcome detection failed: {e}")

            # === Logical Spread Arbitrage (逻辑价差套利) ===
            if lsa_enabled:
                try:
                    # 扫描 Polymarket 的逻辑价差套利
                    lsa_pairs, lsa_detector = scan_logical_spread_arbitrage(
                        poly_markets, 'polymarket', config, lsa_detector
                    )

                    for pair in lsa_pairs:
                        spread_pct = pair.spread * 100
                        if spread_pct >= lsa_min_spread:
                            all_opps.append({
                                'is_logical_spread': True,
                                'pair_obj': pair,
                                'market': f"[{pair.logical_type.value}] {pair.hard_title[:40]}...",
                                'platforms': f"Polymarket (Logical Spread)",
                                'arbitrage': round(spread_pct, 2),
                                'market_key': f"LSA-{pair.pair_key}",
                                'is_real': True,
                            })
                            lsa_count += 1
                except Exception as e:
                    logging.warning(f"  Logical spread detection failed: {e}")

            same_count = sum(1 for o in all_opps if 'SAME-' in o.get('market_key', ''))
            lsa_key_count = sum(1 for o in all_opps if o.get('market_key', '').startswith('LSA-'))
            cross_count = len(all_opps) - same_count - multi_count - combo_count - lsa_key_count
            logger.info(f"  Arbitrage found: {len(all_opps)} total "
                        f"({same_count} same-platform, {cross_count} cross-platform, "
                        f"{multi_count} multi-outcome, {combo_count} cross-combo, {lsa_key_count} logical-spread)")

            # 套利 Telegram 通知已禁用 — 仅保留争议信号通知
            # (套利数据仍然正常扫描和记录日志，供 dashboard 使用)

            # === Dispute Signal Detection (UMA Oracle) ===
            if dispute_enabled and dispute_detector:
                now_ts = time.time()
                if now_ts - last_dispute_scan >= dispute_scan_interval:
                    last_dispute_scan = now_ts
                    try:
                        from src.dispute_signal import format_dispute_signal_message, Severity
                        signals = dispute_detector.detect_signals(poly_markets)
                        dispute_signal_count = len(signals)
                        if dispute_signal_count > 0:
                            logger.info(f"  Dispute signals: {dispute_signal_count}")

                        for sig in signals:
                            if dispute_detector.is_already_notified(sig):
                                continue

                            # 冷却策略: HIGH 立即, MEDIUM 30分钟, LOW 60分钟
                            cooldown_map = {
                                Severity.HIGH: 0,
                                Severity.MEDIUM: dispute_cooldown_minutes,
                                Severity.LOW: dispute_cooldown_minutes * 2,
                            }
                            cooldown_min = cooldown_map.get(sig.severity, 60)

                            if cooldown_min > 0 and sig.signal_key in dispute_notifications:
                                elapsed = datetime.now() - dispute_notifications[sig.signal_key]
                                if elapsed < timedelta(minutes=cooldown_min):
                                    continue

                            msg = format_dispute_signal_message(sig, scan_count)
                            if send_telegram(msg, config):
                                logger.info(f"  TG sent dispute: [{sig.severity.value}] {sig.title[:30]}")
                                dispute_notifications[sig.signal_key] = datetime.now()
                                dispute_detector.mark_notified(sig)
                    except Exception as e:
                        logging.warning(f"  Dispute signal scan failed: {e}")

            if scan_count % 10 == 0:
                logger.info(f"[Stats] {scan_count} scans completed")

            logger.info(f"Next scan in {scan_interval}s...")
            time.sleep(scan_interval)

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
    finally:
        send_telegram(
            f"⏹ <b>Monitor Stopped</b>\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Total scans: {scan_count}",
            config
        )

    print("\nMonitor stopped.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
