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



def find_arbitrage(markets_a, markets_b, name_a, name_b, threshold=2.0, min_confidence=0.2):
    """Find cross-platform arbitrage using MarketMatcher (same algorithm as dashboard)

    Uses the sophisticated MarketMatcher from src/market_matcher.py with:
    - Weighted scoring: Entity (40%) + Number/date (30%) + Vocabulary (20%) + String (10%)
    - Manual mapping support
    - Hard constraints on year/price matching
    """
    try:
        from src.market_matcher import MarketMatcher
    except ImportError:
        logging.error("Cannot import MarketMatcher — cross-platform matching disabled")
        return []

    # Prepare markets with match_title for the matcher
    markets_a_prepared = []
    for m in markets_a:
        m_copy = m.copy()
        m_copy['match_title'] = m.get('match_title', m.get('title', ''))
        markets_a_prepared.append(m_copy)

    markets_b_prepared = []
    for m in markets_b:
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


def format_arb_message(opp, scan_count):
    """Format arbitrage as Telegram message (binary or multi-outcome)"""
    arb_type = opp.get('arb_type', '')

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
    print("  Version: v2.3 (2026-02-26) - 分级冷却播报: 首次立即, ≥1%需30分钟, 0.5-1%需1小时")
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
                opps = find_arbitrage(ma, mb, na, nb, threshold, min_confidence)
                for opp in opps:
                    opp['is_real'] = True  # 跨平台套利数据来自 public API，无需过滤
                all_opps.extend(opps)

            # === Multi-outcome arbitrage (reuse dashboard functions + caches) ===
            multi_count = 0
            combo_count = 0
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

            same_count = sum(1 for o in all_opps if 'SAME-' in o.get('market_key', ''))
            cross_count = len(all_opps) - same_count - multi_count - combo_count
            logger.info(f"  Arbitrage found: {len(all_opps)} total "
                        f"({same_count} same-platform, {cross_count} cross-platform, "
                        f"{multi_count} multi-outcome, {combo_count} cross-combo)")

            # 发送 Telegram 通知（组合方案：首次立即播报 + 分级冷却）
            # - 首次发现 → 立即播报
            # - 大幅变化(≥1%) → 30分钟后可播报
            # - 中等变化(0.5-1%) → 1小时后可播报
            # - 小幅变化(<0.5%) → 不播报
            for opp in all_opps:
                if not opp['is_real']:
                    continue

                # 使用 market_key 作为唯一标识
                market_key = opp.get('market_key', '')
                if not market_key:
                    continue

                should_notify = False
                last_opp = last_sent_opportunities.get(market_key)

                if last_opp is None:
                    # 首次发现这个机会 → 立即播报
                    should_notify = True
                else:
                    # 检查价格变化幅度和时间冷却
                    price_change = abs(opp['arbitrage'] - last_opp['arbitrage'])

                    if price_change < 0.5:
                        # 小幅变化 < 0.5% → 不播报
                        continue
                    elif price_change >= 1.0:
                        # 大幅变化 ≥ 1% → 30分钟冷却
                        min_wait_minutes = 30
                    else:
                        # 中等变化 0.5-1% → 1小时冷却
                        min_wait_minutes = 60

                    # 检查冷却时间
                    if market_key in last_notifications:
                        elapsed = datetime.now() - last_notifications[market_key]
                        if elapsed >= timedelta(minutes=min_wait_minutes):
                            should_notify = True
                            logger.debug(f"  Price changed: {last_opp['arbitrage']:.2f}% -> {opp['arbitrage']:.2f}% (Δ{price_change:.2f}%, elapsed: {elapsed.seconds//60}m)")
                    else:
                        should_notify = True

                if should_notify:
                    msg = format_arb_message(opp, scan_count)
                    if send_telegram(msg, config):
                        label = opp.get('market') or opp.get('event_title', '')
                        logger.info(f"  TG sent: {label[:30]} ({opp['arbitrage']}%)")
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
