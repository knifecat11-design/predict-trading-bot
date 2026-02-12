"""
Cross-platform prediction market arbitrage dashboard
Platforms: Polymarket, Opinion.trade, Predict.fun
"""

import os
import sys
import json
import time
import logging
import threading
import traceback
from datetime import datetime
from flask import Flask, render_template, jsonify

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
logger.info(f"Python path: {sys.path[:3]}")

app = Flask(__name__,
            template_folder=os.path.join(os.path.dirname(__file__), 'templates'))

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
    'last_sent_opportunities': {},  # Track last sent opportunities for deduplication
}
_lock = threading.Lock()


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
        'scan_interval': int(os.getenv('SCAN_INTERVAL', 30)),
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


def fetch_polymarket_data(config):
    """Fetch Polymarket markets (public API, always works)"""
    try:
        from src.polymarket_api import PolymarketClient
        client = PolymarketClient(config)
        # 获取所有标签的市场（覆盖全站）
        markets = client.get_all_tags_markets(limit_per_tag=200)

        parsed = []
        for m in markets[:3000]:
            try:
                outcome_str = m.get('outcomePrices', '[]')
                if isinstance(outcome_str, str):
                    prices = json.loads(outcome_str)
                else:
                    prices = outcome_str
                if len(prices) < 2:
                    continue

                yes_price = float(prices[0])
                no_price = float(prices[1])
                if yes_price <= 0 or no_price <= 0:
                    continue

                parsed.append({
                    'id': m.get('conditionId', m.get('condition_id', '')),
                    'title': m.get('question', '')[:80],
                    'yes': round(yes_price, 4),
                    'no': round(no_price, 4),
                    'volume': float(m.get('volume24hr', 0) or 0),
                    'liquidity': float(m.get('liquidity', 0) or 0),
                    'platform': 'polymarket',
                    'end_date': m.get('endDate', ''),
                })
            except (ValueError, TypeError, KeyError) as e:
                logger.debug(f"解析 Polymarket 市场失败: {e}")
                continue
            except Exception as e:
                logger.warning(f"解析 Polymarket 市场时出现意外错误: {e}")
                continue

        logger.info(f"Polymarket: fetched {len(parsed)} markets")
        return 'active', parsed
    except ImportError as e:
        logger.error(f"Polymarket import error: {e}")
        return 'error', []
    except Exception as e:
        logger.error(f"Polymarket fetch error: {e}")
        return 'error', []


def fetch_opinion_data(config):
    """Fetch Opinion.trade markets"""
    api_key = config.get('opinion', {}).get('api_key', '')
    if not api_key:
        logger.warning("Opinion: no API key")
        return 'no_key', []

    try:
        import requests
        # First test if the API key is valid
        test_url = config.get('opinion', {}).get('base_url', 'https://proxy.opinion.trade:8443/openapi')
        try:
            response = requests.get(
                f"{test_url}/market",
                headers={'apikey': api_key},
                params={'limit': 1},
                timeout=10
            )
            if response.status_code == 401:
                logger.error("Opinion API key is invalid (401)")
                return 'no_key', []
            elif response.status_code != 200:
                logger.warning(f"Opinion API returned {response.status_code}")
        except Exception as e:
            logger.warning(f"Opinion API test failed: {e}")
            # Continue to try full client anyway

        from src.opinion_api import OpinionAPIClient
        client = OpinionAPIClient(config)

        # 直接获取市场列表，已按 24h 交易量排序
        raw_markets = client.get_markets(status='activated', sort_by=5, limit=500)

        if not raw_markets:
            return 'error', []

        parsed = []
        for m in raw_markets:
            try:
                market_id = str(m.get('marketId', ''))
                title = m.get('marketTitle', '')
                yes_token = m.get('yesTokenId', '')
                no_token = m.get('noTokenId', '')

                # 独立获取 Yes 和 No 价格（不使用 1-yes 推导）
                yes_price = client.get_token_price(yes_token)

                # 尝试独立获取 No 价格，失败时 fallback 到 1 - yes
                if no_token:
                    no_price = client.get_token_price(no_token)
                    if no_price is None:
                        # Fallback: 使用 1 - yes_price（当 No token 订单簿为空时）
                        logger.debug(f"市场 {market_id} No 价格获取失败，使用 fallback 1 - yes")
                        no_price = round(1.0 - yes_price, 4) if yes_price is not None else None
                else:
                    no_price = None

                # 跳过价格获取失败的市场
                if yes_price is None:
                    continue
                if no_price is None:
                    continue
                if yes_price <= 0 or yes_price >= 1 or no_price <= 0 or no_price >= 1:
                    continue

                parsed.append({
                    'id': market_id,
                    'title': title[:80],
                    'yes': round(yes_price, 4),
                    'no': round(no_price, 4),
                    'volume': float(m.get('volume24h', m.get('volume', 0)) or 0),
                    'liquidity': 0,
                    'platform': 'opinion',
                    'end_date': m.get('cutoff_at', ''),
                })
            except (ValueError, TypeError, KeyError) as e:
                logger.debug(f"解析 Opinion 市场失败: {e}")
                continue
            except Exception as e:
                logger.warning(f"解析 Opinion 市场时出现意外错误: {e}")
                continue

            if len(parsed) >= 200:
                break

        logger.info(f"Opinion: fetched {len(parsed)} markets")
        return 'active', parsed
    except ImportError as e:
        logger.error(f"Opinion import error: {e}")
        return 'error', []
    except Exception as e:
        logger.error(f"Opinion fetch error: {e}")
        return 'error', []


def fetch_predict_data(config):
    """Fetch Predict.fun markets（改进版：独立获取 No 价格）"""
    api_key = config.get('api', {}).get('api_key', '')
    if not api_key:
        return 'no_key', []

    try:
        from src.api_client import PredictAPIClient
        client = PredictAPIClient(config)
        raw_markets = client.get_markets(status='open', limit=50)

        if not raw_markets:
            return 'error', []

        parsed = []
        for m in raw_markets:
            try:
                market_id = m.get('id', m.get('market_id', ''))
                if not market_id:
                    continue

                # 使用新的完整订单簿方法（独立获取 Yes 和 No 价格）
                full_ob = client.get_full_orderbook(market_id)
                if full_ob is None:
                    continue

                yes_price = (full_ob['yes_bid'] + full_ob['yes_ask']) / 2
                no_price = (full_ob['no_bid'] + full_ob['no_ask']) / 2

                parsed.append({
                    'id': market_id,
                    'title': (m.get('question') or m.get('title', ''))[:80],
                    'yes': round(yes_price, 4),
                    'no': round(no_price, 4),
                    'volume': float(m.get('volume', 0) or 0),
                    'liquidity': float(m.get('liquidity', 0) or 0),
                    'platform': 'predict',
                    'end_date': '',  # Predict may not have this field
                })
            except Exception as e:
                logger.debug(f"解析 Predict 市场失败: {e}")
                continue

        logger.info(f"Predict: fetched {len(parsed)} markets")
        return 'active', parsed
    except ImportError as e:
        logger.error(f"Predict import error: {e}")
        return 'error', []
    except Exception as e:
        logger.error(f"Predict fetch error: {e}")
        return 'error', []


def extract_keywords(title):
    """Extract keywords from market title for matching (improved)"""
    import re
    # 扩展停用词列表，提高匹配质量
    stop_words = {
        'will', 'won', 'the', 'a', 'an', 'be', 'by', 'in', 'on', 'at', 'to', 'for',
        'of', 'is', 'it', 'or', 'and', 'not', 'but', 'can', 'has', 'had', 'have',
        'from', 'with', 'this', 'that', 'are', 'was', 'were', 'been', 'being',
        'get', 'got', 'out', 'over', 'than', 'then', 'when', 'what', 'which',
        'while', 'who', 'whom', 'why', 'how', 'all', 'any', 'both', 'each',
        'more', 'most', 'some', 'such', 'your', 'our', 'their', 'its'
    }
    words = re.findall(r'\b\w+\b', title.lower())
    # 只保留长度 > 3 的词（提高质量）
    return {w for w in words if len(w) > 3 and w not in stop_words}


def parse_end_date(date_str):
    """Parse end date string for validation (improved: specific exceptions)"""
    if not date_str:
        return None
    try:
        if isinstance(date_str, str):
            if date_str.isdigit():
                return datetime.fromtimestamp(int(date_str))
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        elif isinstance(date_str, (int, float)):
            return datetime.fromtimestamp(date_str)
    except (ValueError, OSError) as e:
        logger.debug(f"解析结束日期失败: {date_str}, 错误: {e}")
        return None
    except Exception as e:
        logger.warning(f"解析结束日期时出现意外错误: {date_str}, 错误: {e}")
        return None


def find_cross_platform_arbitrage(markets_a, markets_b, platform_a_name, platform_b_name, threshold=2.0):
    """Find arbitrage between two platform market lists"""
    opportunities = []

    for ma in markets_a:
        ka = extract_keywords(ma['title'])
        if not ka:
            continue

        for mb in markets_b:
            kb = extract_keywords(mb['title'])
            if not kb:
                continue

            intersection = ka & kb
            union = ka | kb
            similarity = len(intersection) / len(union) if union else 0

            if similarity < 0.35:  # 提高阈值从 0.2 到 0.35，减少错误匹配
                continue

            # Check end date similarity
            end_a = parse_end_date(ma.get('end_date', ''))
            end_b = parse_end_date(mb.get('end_date', ''))
            if end_a and end_b:
                time_diff = abs((end_a - end_b).days)
                if time_diff > 5:  # More than 5 days difference, skip
                    continue

            # Direction 1: Buy Yes on A + Buy No on B
            combined1 = ma['yes'] + mb['no']
            arb1 = (1.0 - combined1) * 100

            # Direction 2: Buy Yes on B + Buy No on A
            combined2 = mb['yes'] + ma['no']
            arb2 = (1.0 - combined2) * 100

            # Create unique market key for deduplication
            market_key_base = f"{platform_a_name}-{platform_b_name}-{','.join(sorted(intersection))}"

            if arb1 >= threshold:
                opportunities.append({
                    'market': ma['title'],
                    'platform_a': platform_a_name,
                    'platform_b': platform_b_name,
                    'direction': f"{platform_a_name} Buy Yes + {platform_b_name} Buy No",
                    'a_yes': round(ma['yes'] * 100, 2),
                    'a_no': round(ma['no'] * 100, 2),
                    'b_yes': round(mb['yes'] * 100, 2),
                    'b_no': round(mb['no'] * 100, 2),
                    'combined': round(combined1 * 100, 2),
                    'arbitrage': round(arb1, 2),
                    'confidence': round(similarity, 2),
                    'timestamp': datetime.now().strftime('%H:%M:%S'),
                    'market_key': f"{market_key_base}-yes1_no2",
                })

            if arb2 >= threshold:
                opportunities.append({
                    'market': mb['title'],
                    'platform_a': platform_b_name,
                    'platform_b': platform_a_name,
                    'direction': f"{platform_b_name} Buy Yes + {platform_a_name} Buy No",
                    'a_yes': round(mb['yes'] * 100, 2),
                    'a_no': round(mb['no'] * 100, 2),
                    'b_yes': round(ma['yes'] * 100, 2),
                    'b_no': round(ma['no'] * 100, 2),
                    'combined': round(combined2 * 100, 2),
                    'arbitrage': round(arb2, 2),
                    'confidence': round(similarity, 2),
                    'timestamp': datetime.now().strftime('%H:%M:%S'),
                    'market_key': f"{market_key_base}-yes2_no1",
                })

    opportunities.sort(key=lambda x: x['arbitrage'], reverse=True)
    return opportunities


def background_scanner():
    """Background thread that scans all platforms"""
    global _state
    config = load_config()
    threshold = float(config.get('opinion_poly', {}).get('min_arbitrage_threshold', 2.0))
    scan_interval = int(config.get('arbitrage', {}).get('scan_interval', 30))

    logger.info(f"Scanner started: threshold={threshold}%, interval={scan_interval}s")

    while True:
        try:
            # Fetch all platforms
            poly_status, poly_markets = fetch_polymarket_data(config)
            opinion_status, opinion_markets = fetch_opinion_data(config)
            predict_status, predict_markets = fetch_predict_data(config)

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
                now = time.time()
                _state['platforms']['polymarket'] = {
                    'status': poly_status,
                    'markets': poly_markets[:20],
                    'count': len(poly_markets),
                    'last_update': now,
                }
                _state['platforms']['opinion'] = {
                    'status': opinion_status,
                    'markets': opinion_markets[:20],
                    'count': len(opinion_markets),
                    'last_update': now,
                }
                _state['platforms']['predict'] = {
                    'status': predict_status,
                    'markets': predict_markets[:20],
                    'count': len(predict_markets),
                    'last_update': now,
                }

                # Merge with existing arbitrage opportunities, tracking by market_key
                existing_arb = _state.get('arbitrage', [])
                new_arb_map = {opp['market_key']: opp for opp in all_arb if opp.get('market_key')}
                old_arb_map = {opp['market_key']: opp for opp in existing_arb if opp.get('market_key')}

                # For old opportunities not in new scan, keep them (could add expiry logic later)
                for key, old_opp in old_arb_map.items():
                    if key not in new_arb_map:
                        # Keep old opportunity if it still exists
                        new_arb_map[key] = old_opp

                # For opportunities in both new and old, check if price changed significantly
                for key, new_opp in new_arb_map.items():
                    if key in old_arb_map:
                        old_opp = old_arb_map[key]
                        price_change = abs(new_opp['arbitrage'] - old_opp['arbitrage'])
                        # 提高变化阈值从 0.1% 到 0.5%，让价格更新更明显
                        if price_change < 0.5:
                            new_arb_map[key] = old_opp
                        # 总是更新时间戳，显示最新扫描时间
                        new_opp['timestamp'] = datetime.now().strftime('%H:%M:%S')

                _state['arbitrage'] = list(new_arb_map.values())[:50]
                _state['scan_count'] += 1
                _state['last_scan'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                _state['threshold'] = threshold
                _state['error'] = None

            logger.info(
                f"Scan #{_state['scan_count']}: "
                f"Poly={len(poly_markets)} Opinion={len(opinion_markets)} "
                f"Predict={len(predict_markets)} Arb={len(all_arb)}"
            )

        except Exception as e:
            logger.error(f"Scanner error: {e}")
            logger.error(traceback.format_exc())
            with _lock:
                _state['error'] = str(e)

        time.sleep(scan_interval)


@app.route('/')
def index():
    try:
        return render_template('index.html')
    except Exception as e:
        logger.error(f"Template error: {e}")
        return f"<h1>Dashboard Error</h1><p>{e}</p><pre>{traceback.format_exc()}</pre>"


@app.route('/api/state')
def api_state():
    with _lock:
        return jsonify(_state)


@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})


def main():
    logger.info("=" * 60)
    logger.info("  Prediction Market Arbitrage Dashboard")
    logger.info("=" * 60)

    # Start background scanner
    scanner = threading.Thread(target=background_scanner, daemon=True)
    scanner.start()
    logger.info("Background scanner started")

    # Start Flask
    port = int(os.getenv('PORT', 5000))
    logger.info(f"Dashboard starting on http://0.0.0.0:{port}")
    logger.info(f"Templates folder: {app.template_folder}")

    app.run(host='0.0.0.0', port=port, debug=False)


if __name__ == '__main__':
    main()
