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


def strip_html(html_text):
    """Strip HTML tags from text, return plain text"""
    if not html_text:
        return ''
    # Simple HTML strip - remove everything between < and >
    import re
    return re.sub(r'<[^>]+>', '', html_text).strip()


def slugify(text):
    """Convert text to URL-friendly slug format (improved for Predict.fun)"""
    import re
    # Convert to lowercase
    text = text.lower()

    # Remove verbose phrases FIRST (before word removal)
    # These patterns need to be removed as whole phrases
    text = re.sub(r'\bequal\s+(to\s+)?(or\s+)?greater\s+than\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bgreater\s+(than\s+)?(or\s+)?equal\s+(to\s+)?\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bless\s+(than\s+)?(or\s+)?equal\s+(to\s+)?\b', '', text, flags=re.IGNORECASE)

    # Remove common words that clutter URLs
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

    # Remove words as whole words only
    for word in words_to_remove:
        text = re.sub(r'\b' + word + r'\b', '', text, flags=re.IGNORECASE)

    # Remove dollar signs and commas (keep digits together: $1,800 -> 1800)
    text = text.replace('$', '').replace(',', '')

    # Replace special chars with spaces (except digits, letters, hyphens)
    text = re.sub(r'[^\w\s-]', ' ', text)

    # Replace multiple spaces/newlines/underscores with single hyphen
    text = re.sub(r'[\s_]+', '-', text)

    # Remove trailing/leading hyphens and multiple hyphens
    text = re.sub(r'-+', '-', text)
    text = text.strip('-')

    return text


def platform_link_html(platform_name, market_url=None):
    """Generate colored platform link HTML

    Args:
        platform_name: Platform name (Polymarket, Opinion, Predict)
        market_url: Optional specific market URL (overrides default)
    """
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
    # Use market_url if provided, otherwise use platform home
    url = market_url if market_url else platform_urls.get(platform_name, '#')
    return f"<a href='{url}' target='_blank' style='color:{color};font-weight:600;text-decoration:none'>{platform_name}</a>"


def fetch_polymarket_data(config):
    """Fetch Polymarket markets using bestAsk (actual executable price)"""
    try:
        from src.polymarket_api import PolymarketClient
        poly_client = PolymarketClient(config)
        # 获取所有标签的市场（覆盖全站）
        markets = poly_client.get_all_tags_markets(limit_per_tag=200)

        parsed = []
        for m in markets[:3000]:
            try:
                condition_id = m.get('conditionId', m.get('condition_id', ''))
                if not condition_id:
                    continue

                # 获取事件 slug（用于超链接）
                events = m.get('events', [])
                event_slug = events[0].get('slug', '') if events else ''
                if not event_slug:
                    # Fallback: 使用 condition_id
                    event_slug = condition_id

                # 使用 bestAsk（实际的最低卖价）而不是 outcomePrices（中间价）
                # bestAsk 是 Yes token 的买入价
                best_ask = m.get('bestAsk')
                if best_ask is None or best_ask <= 0 or best_ask >= 1:
                    # 如果没有 bestAsk，回退到 outcomePrices
                    outcome_str = m.get('outcomePrices', '[]')
                    if isinstance(outcome_str, str):
                        prices = json.loads(outcome_str)
                    else:
                        prices = outcome_str
                    if len(prices) < 2:
                        continue
                    yes_price = float(prices[0])
                    no_price = float(prices[1])
                else:
                    # 使用 bestAsk 作为 Yes 价格（实际买入价）
                    yes_price = float(best_ask)
                    # No 价格使用 outcomePrices[1] 或者 1 - yes_price
                    outcome_str = m.get('outcomePrices', '[]')
                    if isinstance(outcome_str, str):
                        prices = json.loads(outcome_str)
                    else:
                        prices = outcome_str
                    if len(prices) >= 2:
                        no_price = float(prices[1])
                    else:
                        # Fallback: 使用 1 - yes_price
                        no_price = round(1.0 - yes_price, 4)

                # 验证价格有效性
                if yes_price <= 0 or yes_price >= 1 or no_price <= 0 or no_price >= 1:
                    continue

                parsed.append({
                    'id': condition_id,
                    'title': f"<a href='https://polymarket.com/event/{event_slug}' target='_blank' style='color:#03a9f4;font-weight:600'>{m.get('question', '')[:80]}</a>",
                    'url': f"https://polymarket.com/event/{event_slug}",  # Add URL field
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

        logger.info(f"Polymarket: fetched {len(parsed)} markets using bestAsk")
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

        logger.info(f"Opinion: 获取到 {len(raw_markets)} 个原始市场，开始解析价格...")

        parsed = []
        # 优化：只对前 50 个高交易量市场获取独立价格，避免阻塞太久
        max_detailed_fetch = 50

        for idx, m in enumerate(raw_markets):
            try:
                market_id = str(m.get('marketId', ''))
                title = m.get('marketTitle', '')
                yes_token = m.get('yesTokenId', '')
                no_token = m.get('noTokenId', '')

                # 独立获取 Yes 价格和订单簿 size（必需）
                orderbook = client.get_order_book(yes_token)
                if orderbook is None or orderbook.yes_ask_size is None:
                    continue

                yes_price = orderbook.yes_ask
                yes_shares = orderbook.yes_ask_size  # 可买份额

                # 对于前 N 个市场，尝试独立获取 No 价格和订单簿 size
                # 对于其他市场，直接用 1 - yes_price 估算（避免太多 HTTP 请求）
                if idx < max_detailed_fetch and no_token:
                    no_price = client.get_token_price(no_token)
                    no_orderbook = client.get_order_book(no_token)
                    no_shares = no_orderbook.yes_ask_size if no_orderbook else 0
                    if no_price is None:
                        # Fallback: 使用 1 - yes_price
                        logger.debug(f"市场 {market_id} No 价格获取失败，使用 fallback 1 - yes")
                        no_price = round(1.0 - yes_price, 4)
                        no_shares = 0
                elif no_token:
                    # 对于后续市场，直接用 1 - yes_price 估算
                    no_price = round(1.0 - yes_price, 4)
                    no_shares = 0
                else:
                    no_price = None
                    no_shares = 0

                # 跳过无效价格
                if no_price is None:
                    continue
                if yes_price <= 0 or yes_price >= 1 or no_price <= 0 or no_price >= 1:
                    continue

                parsed.append({
                    'id': market_id,
                    'title': f"<a href='https://app.opinion.trade/detail?topicId={market_id}' target='_blank' style='color:#d29922;font-weight:600'>{title[:80]}</a>",
                    'url': f"https://app.opinion.trade/detail?topicId={market_id}",  # Correct format
                    'yes': round(yes_price, 4),
                    'no': round(no_price, 4),
                    'amount': yes_shares,  # 订单簿可买份额
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

                question_text = (m.get('question') or m.get('title', ''))
                market_slug = slugify(question_text)
                parsed.append({
                    'id': market_id,
                    'title': f"<a href='https://predict.fun/market/{market_slug}' target='_blank' style='color:#9c27b0;font-weight:600'>{question_text[:80]}</a>",
                    'url': f"https://predict.fun/market/{market_slug}",  # Add URL field with slug
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





def find_cross_platform_arbitrage(markets_a, markets_b, platform_a_name, platform_b_name, threshold=2.0):
    """Find arbitrage between two platform market lists (使用统一匹配模块）"""
    from src.market_matcher import MarketMatcher

    opportunities = []
    checked_pairs = 0
    skipped_similarity = 0
    skipped_end_date = 0

    # 使用统一匹配器
    matcher = MarketMatcher({})
    matched_pairs = matcher.match_markets_cross_platform(
        markets_a, markets_b,
        title_field_a='title', title_field_b='title',
        id_field_a='id', id_field_b='id',
        platform_a=platform_a_name.lower(), platform_b=platform_b_name.lower(),
        min_similarity=0.35,
    )

    logger.info(f"[{platform_a_name} vs {platform_b_name}] MarketMatcher 找到 {len(matched_pairs)} 对匹配")

    for ma, mb, confidence in matched_pairs:
        checked_pairs += 1

        # Check end date similarity (如果有的话)
        end_date_a = ma.get('end_date', '')
        end_date_b = mb.get('end_date', '')
        if end_date_a and end_date_b:
            try:
                from datetime import datetime
                # 尝试解析日期
                if isinstance(end_date_a, str):
                    end_a = datetime.fromisoformat(end_date_a.replace('Z', '+00:00'))
                else:
                    end_a = end_date_a
                if isinstance(end_date_b, str):
                    end_b = datetime.fromisoformat(end_date_b.replace('Z', '+00:00'))
                else:
                    end_b = end_date_b

                time_diff = abs((end_a - end_b).days)
                if time_diff > 30:  # 30天容忍度
                    skipped_end_date += 1
                    continue
            except Exception as e:
                logger.debug(f"End date parsing failed: {e}")
                # 如果日期解析失败，继续检查套利

        # Direction 1: Buy Yes on A + Buy No on B
        # 使用 ask 价格（买入成本）
        combined1 = ma['yes'] + mb['no']
        arb1 = (1.0 - combined1) * 100

        # Direction 2: Buy Yes on B + Buy No on A
        # 使用 ask 价格（买入成本）
        combined2 = mb['yes'] + ma['no']
        arb2 = (1.0 - combined2) * 100

        # Create unique market key for deduplication
        market_key_base = f"{platform_a_name}-{platform_b_name}-{ma.get('id','')}-{mb.get('id','')}"

        if arb1 >= threshold:
            opportunities.append({
                'market': strip_html(ma['title']),  # Strip HTML, plain text
                'platform_a': platform_link_html(platform_a_name, ma.get('url')),  # Colored link with market URL
                'platform_b': platform_link_html(platform_b_name, mb.get('url')),  # Colored link with market URL
                'direction': f"{platform_a_name} Buy Yes + {platform_b_name} Buy No",
                'a_yes': round(ma['yes'] * 100, 2),
                'a_no': round(ma['no'] * 100, 2),
                'b_yes': round(mb['yes'] * 100, 2),
                'b_no': round(mb['no'] * 100, 2),
                'combined': round(combined1 * 100, 2),
                'arbitrage': round(arb1, 2),
                'confidence': round(confidence, 2),
                'timestamp': datetime.now().strftime('%H:%M:%S'),
                'market_key': f"{market_key_base}-yes1_no2",
            })

        if arb2 >= threshold:
            opportunities.append({
                'market': strip_html(mb['title']),  # Strip HTML, plain text
                'platform_a': platform_link_html(platform_b_name, mb.get('url')),  # Colored link with market URL
                'platform_b': platform_link_html(platform_a_name, ma.get('url')),  # Colored link with market URL
                'direction': f"{platform_b_name} Buy Yes + {platform_a_name} Buy No",
                'a_yes': round(mb['yes'] * 100, 2),
                'a_no': round(mb['no'] * 100, 2),
                'b_yes': round(ma['yes'] * 100, 2),
                'b_no': round(ma['no'] * 100, 2),
                'combined': round(combined2 * 100, 2),
                'arbitrage': round(arb2, 2),
                'confidence': round(confidence, 2),
                'timestamp': datetime.now().strftime('%H:%M:%S'),
                'market_key': f"{market_key_base}-yes2_no1",
            })

    opportunities.sort(key=lambda x: x['arbitrage'], reverse=True)

    # Debug logging for arbitrage matching
    logger.info(
        f"[{platform_a_name} vs {platform_b_name}] Checked: {checked_pairs}, "
        f"Skipped(similarity): {skipped_similarity}, "
        f"Skipped(end_date): {skipped_end_date}, "
        f"Found: {len(opportunities)} opportunities"
    )

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
            # 优化：各平台独立更新状态，避免互相阻塞
            with _lock:
                now = time.time()

            # Fetch Polymarket（立即更新状态，不等其他平台）
            poly_status, poly_markets = fetch_polymarket_data(config)
            with _lock:
                _state['platforms']['polymarket'] = {
                    'status': poly_status,
                    'markets': poly_markets[:20],
                    'count': len(poly_markets),
                    'last_update': now,
                }
                logger.info(f"[Polymarket] 已更新: {len(poly_markets)} 个市场, status={poly_status}")

            # Fetch Opinion（立即更新状态，不等其他平台）
            opinion_status, opinion_markets = fetch_opinion_data(config)
            with _lock:
                _state['platforms']['opinion'] = {
                    'status': opinion_status,
                    'markets': opinion_markets[:20],
                    'count': len(opinion_markets),
                    'last_update': now,
                }
                logger.info(f"[Opinion] 已更新: {len(opinion_markets)} 个市场, status={opinion_status}")

            # Fetch Predict（立即更新状态，不等其他平台）
            predict_status, predict_markets = fetch_predict_data(config)
            with _lock:
                _state['platforms']['predict'] = {
                    'status': predict_status,
                    'markets': predict_markets[:20],
                    'count': len(predict_markets),
                    'last_update': now,
                }
                logger.info(f"[Predict] 已更新: {len(predict_markets)} 个市场, status={predict_status}")

            # Find arbitrage across all pairs（所有平台数据已独立更新）
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
