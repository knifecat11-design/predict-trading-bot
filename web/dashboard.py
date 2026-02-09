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
from datetime import datetime
from flask import Flask, render_template, jsonify

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

app = Flask(__name__)
logger = logging.getLogger(__name__)

# Global state
_state = {
    'platforms': {
        'polymarket': {'status': 'unknown', 'markets': [], 'last_update': 0},
        'opinion': {'status': 'unknown', 'markets': [], 'last_update': 0},
        'predict': {'status': 'unknown', 'markets': [], 'last_update': 0},
    },
    'arbitrage': [],
    'scan_count': 0,
    'started_at': datetime.now().isoformat(),
}
_lock = threading.Lock()


def load_config():
    """Load config"""
    try:
        from src.config_helper import load_config as load_env_config
        return load_env_config()
    except ImportError:
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        return {}


def fetch_polymarket_data(config):
    """Fetch Polymarket markets (public API, always works)"""
    try:
        from src.polymarket_api import RealPolymarketClient
        client = RealPolymarketClient(config)
        markets = client.get_all_markets(limit=100, active_only=True)

        parsed = []
        for m in markets:
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
                })
            except:
                continue

        return 'active', parsed
    except Exception as e:
        logger.error(f"Polymarket fetch error: {e}")
        return 'error', []


def fetch_opinion_data(config):
    """Fetch Opinion.trade markets"""
    api_key = config.get('opinion', {}).get('api_key', '')
    if not api_key:
        return 'no_key', []

    try:
        from src.opinion_api import OpinionAPIClient
        client = OpinionAPIClient(config)
        raw_markets = client.get_markets(status='activated', limit=100)

        if not raw_markets:
            return 'error', []

        parsed = []
        for m in raw_markets:
            try:
                market_id = str(m.get('marketId', ''))
                title = m.get('marketTitle', '')
                yes_token = m.get('yesTokenId', '')
                no_token = m.get('noTokenId', '')

                # Try to get price
                yes_price = client.get_token_price(yes_token)
                if yes_price is None:
                    yes_price = 0.5
                no_price = 1.0 - yes_price

                parsed.append({
                    'id': market_id,
                    'title': title[:80],
                    'yes': round(yes_price, 4),
                    'no': round(no_price, 4),
                    'volume': float(m.get('volume24h', m.get('volume', 0)) or 0),
                    'liquidity': 0,
                    'platform': 'opinion',
                })
            except:
                continue

            if len(parsed) >= 30:
                break

        return 'active', parsed
    except Exception as e:
        logger.error(f"Opinion fetch error: {e}")
        return 'error', []


def fetch_predict_data(config):
    """Fetch Predict.fun markets"""
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
                orderbook = m.get('orderBook', {})
                bids = orderbook.get('bids', [])
                asks = orderbook.get('asks', [])
                if not bids or not asks:
                    continue

                yes_bid = float(bids[0]['price'])
                yes_ask = float(asks[0]['price'])
                yes_price = (yes_bid + yes_ask) / 2
                no_price = 1.0 - yes_price

                parsed.append({
                    'id': m.get('id', m.get('market_id', '')),
                    'title': (m.get('question') or m.get('title', ''))[:80],
                    'yes': round(yes_price, 4),
                    'no': round(no_price, 4),
                    'volume': float(m.get('volume', 0) or 0),
                    'liquidity': float(m.get('liquidity', 0) or 0),
                    'platform': 'predict',
                })
            except:
                continue

        return 'active', parsed
    except Exception as e:
        logger.error(f"Predict fetch error: {e}")
        return 'error', []


def extract_keywords(title):
    """Extract keywords from market title for matching"""
    import re
    stop_words = {'will', 'the', 'a', 'an', 'be', 'by', 'in', 'on', 'at', 'to', 'for', 'of', 'is', 'it', 'or', 'and'}
    words = re.findall(r'\b\w+\b', title.lower())
    return {w for w in words if len(w) > 2 and w not in stop_words}


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

            if similarity < 0.2:
                continue

            # Direction 1: Buy Yes on A + Buy No on B
            combined1 = ma['yes'] + mb['no']
            arb1 = (1.0 - combined1) * 100

            # Direction 2: Buy Yes on B + Buy No on A
            combined2 = mb['yes'] + ma['no']
            arb2 = (1.0 - combined2) * 100

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
                })

    opportunities.sort(key=lambda x: x['arbitrage'], reverse=True)
    return opportunities


def background_scanner():
    """Background thread that scans all platforms"""
    global _state
    config = load_config()
    threshold = float(config.get('opinion_poly', {}).get('min_arbitrage_threshold', 2.0))
    scan_interval = int(config.get('arbitrage', {}).get('scan_interval', 30))

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
                _state['arbitrage'] = all_arb[:50]
                _state['scan_count'] += 1
                _state['last_scan'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                _state['threshold'] = threshold

            logger.info(
                f"Scan #{_state['scan_count']}: "
                f"Poly={len(poly_markets)} Opinion={len(opinion_markets)} "
                f"Predict={len(predict_markets)} Arb={len(all_arb)}"
            )

        except Exception as e:
            logger.error(f"Scanner error: {e}")

        time.sleep(scan_interval)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/state')
def api_state():
    with _lock:
        return jsonify(_state)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    # Start background scanner
    scanner = threading.Thread(target=background_scanner, daemon=True)
    scanner.start()
    logger.info("Background scanner started")

    # Start Flask
    port = int(os.getenv('PORT', 5000))
    logger.info(f"Dashboard: http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)


if __name__ == '__main__':
    main()
