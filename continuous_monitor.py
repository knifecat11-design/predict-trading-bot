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

def extract_keywords(title):
    """提取关键词（改进版）"""
    import re
    # 扩展停用词列表，提高匹配质量
    stop_words = {
        'will', 'won', 'the', 'a', 'an', 'be', 'by', 'in', 'on', 'at', 'to', 'for',
        'of', 'is', 'it', 'or', 'and', 'not', 'but', 'can', 'has', 'had', 'have',
        'from', 'with', 'this', 'that', 'are', 'was', 'were', 'been', 'being',
        'get', 'got', 'out', 'over', 'than', 'then', 'when', 'what', 'which'
    }
    words = re.findall(r'\b\w+\b', title.lower())
    # 只保留长度 > 3 的词（提高质量）
    return {w for w in words if len(w) > 3 and w not in stop_words}


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
                'title': title_plain[:80],
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
                'title': title_plain[:80],
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
        return client.get_markets_for_arbitrage(limit=200)
    except Exception as e:
        logging.error(f"Probable fetch: {e}")
        return []


def parse_end_date(date_str):
    """解析结束日期字符串（改进版：添加具体异常处理）"""
    if not date_str:
        return None
    try:
        # 尝试 ISO 格式
        if isinstance(date_str, str):
            # 处理 Unix 时间戳（秒）
            if date_str.isdigit():
                return datetime.fromtimestamp(int(date_str))
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        elif isinstance(date_str, (int, float)):
            return datetime.fromtimestamp(date_str)
    except (ValueError, OSError) as e:
        logging.debug(f"解析结束日期失败: {date_str}, 错误: {e}")
        return None
    except Exception as e:
        logging.warning(f"解析结束日期时出现意外错误: {date_str}, 错误: {e}")
        return None


def find_arbitrage(markets_a, markets_b, name_a, name_b, threshold=2.0, min_confidence=0.2):
    """Find cross-platform arbitrage opportunities (one-to-one matching)

    Uses greedy best-match-first to prevent the same market from matching
    multiple targets (which causes false arbitrage from mismatched markets).
    Uses match_title (with event context) when available.
    """
    # Phase 1: Find all potential matches with similarity scores
    all_matches = []
    for i, ma in enumerate(markets_a):
        ka = extract_keywords(ma.get('match_title', ma['title']))
        if not ka:
            continue
        for j, mb in enumerate(markets_b):
            kb = extract_keywords(mb.get('match_title', mb['title']))
            if not kb:
                continue

            inter = ka & kb
            union = ka | kb
            sim = len(inter) / len(union) if union else 0
            if sim < 0.35:
                continue

            # End date check
            end_a = parse_end_date(ma.get('end_date', ''))
            end_b = parse_end_date(mb.get('end_date', ''))
            if end_a and end_b:
                time_diff = abs((end_a - end_b).days)
                if time_diff > 5:
                    continue

            all_matches.append((i, j, sim, inter))

    # Phase 2: One-to-one matching (greedy, highest similarity first)
    # Prevents market A matching multiple B targets → false arbitrage
    all_matches.sort(key=lambda x: x[2], reverse=True)
    matched_a = set()
    matched_b = set()

    results = []
    for i, j, sim, inter in all_matches:
        if i in matched_a or j in matched_b:
            continue
        matched_a.add(i)
        matched_b.add(j)

        ma, mb = markets_a[i], markets_b[j]
        market_key = f"{name_a}-{name_b}-{','.join(sorted(inter))}"

        comb1 = ma['yes'] + mb['no']
        arb1 = (1.0 - comb1) * 100

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
                    'market_key': market_key,
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
    print("  Polymarket | Opinion.trade | Predict.fun | Probable | Kalshi")
    print("  Version: v2.2 (2026-02-25) - Added Probable.markets")
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
    logger.info(f"  Kalshi:     {'Active' if api_status['kalshi'] else 'Inactive'}")
    logger.info(f"  Probable:   {'Active' if api_status['probable'] else 'Inactive'}")
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
    last_sent_opportunities = {}  # 存储上次发送的机会详情

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

            # Scan all pairs (only between active platforms with real data)
            all_opps = []

            pairs = []
            if poly_markets and opinion_markets:
                pairs.append((poly_markets, opinion_markets, 'Polymarket', 'Opinion', True))
            if poly_markets and predict_markets:
                pairs.append((poly_markets, predict_markets, 'Polymarket', 'Predict', api_status['predict']))
            if poly_markets and kalshi_markets:
                pairs.append((poly_markets, kalshi_markets, 'Polymarket', 'Kalshi', True))
            if poly_markets and probable_markets:
                pairs.append((poly_markets, probable_markets, 'Polymarket', 'Probable', api_status['probable']))
            if opinion_markets and predict_markets:
                pairs.append((opinion_markets, predict_markets, 'Opinion', 'Predict',
                              api_status['opinion'] and api_status['predict']))
            if opinion_markets and kalshi_markets:
                pairs.append((opinion_markets, kalshi_markets, 'Opinion', 'Kalshi', api_status['opinion']))
            if opinion_markets and probable_markets:
                pairs.append((opinion_markets, probable_markets, 'Opinion', 'Probable',
                              api_status['opinion'] and api_status['probable']))
            if predict_markets and kalshi_markets:
                pairs.append((predict_markets, kalshi_markets, 'Predict', 'Kalshi', api_status['predict']))
            if predict_markets and probable_markets:
                pairs.append((predict_markets, probable_markets, 'Predict', 'Probable',
                              api_status['predict'] and api_status['probable']))
            if kalshi_markets and probable_markets:
                pairs.append((kalshi_markets, probable_markets, 'Kalshi', 'Probable', api_status['probable']))

            for ma, mb, na, nb, is_real in pairs:
                opps = find_arbitrage(ma, mb, na, nb, threshold, min_confidence)
                if opps:
                    logger.info(f"  {na} <-> {nb}: {len(opps)} opportunities")

                for opp in opps:
                    opp['is_real'] = is_real
                    all_opps.append(opp)

            # 发送 Telegram 通知（带去重逻辑）
            for opp in all_opps:
                if not opp['is_real']:
                    continue

                # 使用 market_key 作为唯一标识
                market_key = opp.get('market_key', '')
                if not market_key:
                    continue

                # 检查是否需要通知（价格变化超过 5% 或者首次发送）
                should_notify = False
                last_opp = last_sent_opportunities.get(market_key)

                if last_opp is None:
                    # 首次发现这个机会
                    should_notify = True
                else:
                    # 检查价格变化是否超过阈值
                    price_change = abs(opp['arbitrage'] - last_opp['arbitrage'])
                    if price_change >= 0.5:  # 价格变化超过 0.5%（从 0.1% 提高）
                        should_notify = True
                        logger.debug(f"  Price changed: {last_opp['arbitrage']:.2f}% -> {opp['arbitrage']:.2f}% (Δ{price_change:.2f}%)")

                # 冷却时间检查
                if market_key in last_notifications:
                    if datetime.now() - last_notifications[market_key] < timedelta(minutes=cooldown_minutes):
                        continue

                if should_notify:
                    msg = format_arb_message(opp, scan_count)
                    if send_telegram(msg, config):
                        logger.info(f"  TG sent: {opp['market'][:30]} ({opp['arbitrage']}%)")
                        last_notifications[market_key] = datetime.now()
                        last_sent_opportunities[market_key] = opp.copy()  # 更新最后发送的机会

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
