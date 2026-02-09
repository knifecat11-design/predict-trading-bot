"""
Cross-platform prediction market arbitrage monitor
Platforms: Polymarket, Opinion.trade, Predict.fun
Only sends Telegram notifications for real (non-mock) data
"""

import os
import sys
import time
import json
import logging
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
        'predict': False,  # Currently no valid API key
    }

    # Check Predict.fun API
    predict_key = config.get('api', {}).get('api_key', '')
    if predict_key:
        try:
            import requests
            base_url = config.get('api', {}).get('base_url', 'https://api.predict.fun')
            resp = requests.get(
                f"{base_url}/markets",
                headers={'Authorization': f'Bearer {predict_key}'},
                params={'limit': 1},
                timeout=5
            )
            status['predict'] = resp.status_code == 200
        except:
            status['predict'] = False

    return status


# ============================================================
# Cross-platform arbitrage scanning
# ============================================================

def extract_keywords(title):
    import re
    stop_words = {'will', 'the', 'a', 'an', 'be', 'by', 'in', 'on', 'at', 'to', 'for', 'of', 'is', 'it'}
    words = re.findall(r'\b\w+\b', title.lower())
    return {w for w in words if len(w) > 2 and w not in stop_words}


def fetch_polymarket_markets(config):
    """Fetch Polymarket markets (always real data)"""
    try:
        from src.polymarket_api import RealPolymarketClient
        client = RealPolymarketClient(config)
        markets = client.get_all_markets(limit=150, active_only=True)

        parsed = []
        for m in markets:
            try:
                prices_str = m.get('outcomePrices', '[]')
                prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
                if len(prices) < 2:
                    continue
                yes = float(prices[0])
                no = float(prices[1])
                if yes <= 0 or no <= 0:
                    continue
                parsed.append({
                    'title': m.get('question', '')[:80],
                    'yes': yes, 'no': no,
                    'volume': float(m.get('volume24hr', 0) or 0),
                })
            except:
                continue
        return parsed
    except Exception as e:
        logging.error(f"Polymarket fetch: {e}")
        return []


def fetch_opinion_markets(config):
    """Fetch Opinion markets (requires API key)"""
    try:
        from src.opinion_api import OpinionAPIClient
        client = OpinionAPIClient(config)
        raw = client.get_markets(status='activated', limit=100)

        parsed = []
        for m in raw:
            try:
                yes_token = m.get('yesTokenId', '')
                yes_price = client.get_token_price(yes_token)
                if yes_price is None:
                    continue
                parsed.append({
                    'title': m.get('marketTitle', '')[:80],
                    'yes': yes_price,
                    'no': 1.0 - yes_price,
                    'volume': float(m.get('volume24h', 0) or 0),
                })
            except:
                continue
            if len(parsed) >= 30:
                break
        return parsed
    except Exception as e:
        logging.error(f"Opinion fetch: {e}")
        return []


def fetch_predict_markets(config):
    """Fetch Predict.fun markets (requires API key)"""
    try:
        from src.api_client import PredictAPIClient
        client = PredictAPIClient(config)
        raw = client.get_markets(status='open', limit=50)

        parsed = []
        for m in raw:
            try:
                ob = m.get('orderBook', {})
                bids = ob.get('bids', [])
                asks = ob.get('asks', [])
                if not bids or not asks:
                    continue
                yes = (float(bids[0]['price']) + float(asks[0]['price'])) / 2
                parsed.append({
                    'title': (m.get('question') or m.get('title', ''))[:80],
                    'yes': yes, 'no': 1.0 - yes,
                    'volume': float(m.get('volume', 0) or 0),
                })
            except:
                continue
        return parsed
    except Exception as e:
        logging.error(f"Predict fetch: {e}")
        return []


def find_arbitrage(markets_a, markets_b, name_a, name_b, threshold=2.0, min_confidence=0.2):
    """Find cross-platform arbitrage opportunities"""
    results = []

    for ma in markets_a:
        ka = extract_keywords(ma['title'])
        if not ka:
            continue
        for mb in markets_b:
            kb = extract_keywords(mb['title'])
            if not kb:
                continue

            inter = ka & kb
            union = ka | kb
            sim = len(inter) / len(union) if union else 0
            if sim < min_confidence:
                continue

            # Direction 1: A Yes + B No
            comb1 = ma['yes'] + mb['no']
            arb1 = (1.0 - comb1) * 100

            # Direction 2: B Yes + A No
            comb2 = mb['yes'] + ma['no']
            arb2 = (1.0 - comb2) * 100

            for arb_pct, direction, market_title, ya, na, yb, nb in [
                (arb1, f"{name_a} Buy Yes + {name_b} Buy No", ma['title'], ma['yes'], ma['no'], mb['yes'], mb['no']),
                (arb2, f"{name_b} Buy Yes + {name_a} Buy No", mb['title'], mb['yes'], mb['no'], ma['yes'], ma['no']),
            ]:
                if arb_pct >= threshold:
                    results.append({
                        'market': market_title,
                        'platforms': f"{name_a} <-> {name_b}",
                        'direction': direction,
                        'arbitrage': round(arb_pct, 2),
                        'a_yes': round(ya * 100, 2), 'a_no': round(na * 100, 2),
                        'b_yes': round(yb * 100, 2), 'b_no': round(nb * 100, 2),
                        'confidence': round(sim, 2),
                    })

    results.sort(key=lambda x: x['arbitrage'], reverse=True)
    return results


def format_arb_message(opp, scan_count):
    """Format arbitrage as Telegram message"""
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
    print("  Polymarket | Opinion.trade | Predict.fun")
    print("=" * 70)
    print()

    logger = setup_logging()
    config = load_config()

    # Config
    arb_config = config.get('arbitrage', {})
    op_config = config.get('opinion_poly', {})
    scan_interval = arb_config.get('scan_interval', 30)
    cooldown_minutes = arb_config.get('cooldown_minutes', 10)
    threshold = op_config.get('min_arbitrage_threshold', 2.0)
    min_confidence = op_config.get('min_confidence', 0.2)

    # Check API status
    api_status = check_platform_api(config)
    logger.info(f"Platform API Status:")
    logger.info(f"  Polymarket: {'Active' if api_status['polymarket'] else 'Inactive'}")
    logger.info(f"  Opinion:    {'Active' if api_status['opinion'] else 'No API Key'}")
    logger.info(f"  Predict:    {'Active' if api_status['predict'] else 'No API Key'}")
    logger.info(f"Threshold: {threshold}%  Interval: {scan_interval}s  Cooldown: {cooldown_minutes}m")
    logger.info("")

    # Startup notification
    active_platforms = [k for k, v in api_status.items() if v]
    sent = send_telegram(
        f"🚀 <b>Arbitrage Monitor Started</b>\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Active: {', '.join(active_platforms)}\n"
        f"Threshold: {threshold}%",
        config
    )
    logger.info(f"Startup notification: {'sent' if sent else 'failed (rate limited?)'}")

    # State
    running = True
    scan_count = 0
    last_notifications = {}

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

            logger.info(f"  Polymarket: {len(poly_markets)}  Opinion: {len(opinion_markets)}  Predict: {len(predict_markets)}")

            # Scan all pairs (only between active platforms with real data)
            all_opps = []

            pairs = []
            if poly_markets and opinion_markets:
                pairs.append((poly_markets, opinion_markets, 'Polymarket', 'Opinion', True))
            if poly_markets and predict_markets:
                pairs.append((poly_markets, predict_markets, 'Polymarket', 'Predict', api_status['predict']))
            if opinion_markets and predict_markets:
                pairs.append((opinion_markets, predict_markets, 'Opinion', 'Predict',
                              api_status['opinion'] and api_status['predict']))

            for ma, mb, na, nb, is_real in pairs:
                opps = find_arbitrage(ma, mb, na, nb, threshold, min_confidence)
                if opps:
                    logger.info(f"  {na} <-> {nb}: {len(opps)} opportunities")

                for opp in opps:
                    opp['is_real'] = is_real
                    all_opps.append(opp)

            # Send Telegram only for REAL data opportunities
            for opp in all_opps:
                if not opp['is_real']:
                    logger.debug(f"  Skip mock: {opp['market'][:30]}")
                    continue

                market_key = f"{opp['platforms']}:{opp['market'][:30]}"
                now = datetime.now()

                if market_key in last_notifications:
                    if now - last_notifications[market_key] < timedelta(minutes=cooldown_minutes):
                        continue

                msg = format_arb_message(opp, scan_count)
                if send_telegram(msg, config):
                    logger.info(f"  TG sent: {opp['market'][:30]} ({opp['arbitrage']}%)")
                    last_notifications[market_key] = now

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
