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
    """Fetch Polymarket markets — 参考 continuous_monitor 直接用 API 响应数据，无需逐个请求订单簿"""
    try:
        from src.polymarket_api import PolymarketClient
        poly_client = PolymarketClient(config)
        # 获取所有标签的市场（覆盖全站，~9 个 HTTP 请求）
        markets = poly_client.get_all_tags_markets(limit_per_tag=200)

        parsed = []
        for m in markets:
            try:
                condition_id = m.get('conditionId', m.get('condition_id', ''))
                if not condition_id:
                    continue

                # 优先使用 bestBid/bestAsk（API 响应中已包含，无需额外 HTTP 请求）
                best_bid = m.get('bestBid')
                best_ask = m.get('bestAsk')

                if best_bid is not None and best_ask is not None:
                    yes_price = float(best_ask)      # Yes 买入价 = best ask
                    no_price = 1.0 - float(best_bid)  # No 买入价 = 1 - best bid
                else:
                    # Fallback: 使用 outcomePrices（参考 continuous_monitor.py）
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

                # 获取事件 slug（用于超链接）
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
            except (ValueError, TypeError, KeyError) as e:
                logger.debug(f"解析 Polymarket 市场失败: {e}")
                continue
            except Exception as e:
                logger.warning(f"解析 Polymarket 市场时出现意外错误: {e}")
                continue

        logger.info(f"Polymarket: fetched {len(parsed)} markets (0 extra HTTP requests)")
        return 'active', parsed
    except ImportError as e:
        logger.error(f"Polymarket import error: {e}")
        return 'error', []
    except Exception as e:
        logger.error(f"Polymarket fetch error: {e}")
        return 'error', []


def fetch_opinion_data(config):
    """Fetch Opinion.trade markets — 参考 continuous_monitor 的 get_token_price + fallback 策略"""
    api_key = config.get('opinion', {}).get('api_key', '')
    if not api_key:
        logger.warning("Opinion: no API key")
        return 'no_key', []

    try:
        # 不做冗余 test 请求（成功脚本也没有），直接创建客户端
        # get_markets() 内部会处理 401/429 等错误
        from src.opinion_api import OpinionAPIClient
        client = OpinionAPIClient(config)

        # 直接获取市场列表，已按 24h 交易量排序
        raw_markets = client.get_markets(status='activated', sort_by=5, limit=500)

        if not raw_markets:
            return 'error', []

        logger.info(f"Opinion: 获取到 {len(raw_markets)} 个原始市场，开始解析价格...")

        parsed = []
        # 参考 continuous_monitor.py: 前 N 个市场独立获取 No 价格，后续用 fallback
        max_detailed_fetch = 80  # 前 80 个独立获取 No（比 continuous_monitor 的 50 多）

        for idx, m in enumerate(raw_markets):
            try:
                market_id = str(m.get('marketId', ''))
                title = m.get('marketTitle', '')
                yes_token = m.get('yesTokenId', '')
                no_token = m.get('noTokenId', '')

                # 跳过无效 token（避免浪费 HTTP 请求）
                if not yes_token:
                    continue

                # 使用 get_token_price（轻量级，参考 continuous_monitor）
                yes_price = client.get_token_price(yes_token)
                if yes_price is None:
                    continue

                # 前 N 个市场独立获取 No 价格，后续用 1-yes fallback
                if idx < max_detailed_fetch and no_token:
                    no_price = client.get_token_price(no_token)
                    if no_price is None:
                        no_price = round(1.0 - yes_price, 4)
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
            except (ValueError, TypeError, KeyError) as e:
                logger.debug(f"解析 Opinion 市场失败: {e}")
                continue
            except Exception as e:
                logger.warning(f"解析 Opinion 市场时出现意外错误: {e}")
                continue

            if len(parsed) >= 300:
                break

        logger.info(f"Opinion: fetched {len(parsed)} markets (详细价格: 前{max_detailed_fetch}个, fallback: 其余)")
        return 'active', parsed
    except ImportError as e:
        logger.error(f"Opinion import error: {e}")
        return 'error', []
    except Exception as e:
        logger.error(f"Opinion fetch error: {e}")
        return 'error', []


def fetch_predict_data(config):
    """Fetch Predict.fun markets — limit 提高到 200，前 80 个获取精确价格"""
    api_key = config.get('api', {}).get('api_key', '')
    if not api_key:
        return 'no_key', []

    try:
        from src.api_client import PredictAPIClient
        client = PredictAPIClient(config)

        # 分页获取更多市场（API 单次最多 100）
        all_raw = []
        for sort_by in ['popular', 'newest']:
            batch = client.get_markets(status='open', sort=sort_by, limit=100)
            for m in batch:
                mid = m.get('id', m.get('market_id', ''))
                if mid and mid not in {x.get('id', x.get('market_id', '')) for x in all_raw}:
                    all_raw.append(m)

        if not all_raw:
            return 'error', []

        logger.info(f"Predict: 获取到 {len(all_raw)} 个原始市场")

        parsed = []
        max_detailed_fetch = 80  # 前 80 个获取精确订单簿价格

        for idx, m in enumerate(all_raw):
            try:
                market_id = m.get('id', m.get('market_id', ''))
                if not market_id:
                    continue

                question_text = (m.get('question') or m.get('title', ''))

                if idx < max_detailed_fetch:
                    # 精确模式：使用 best ask
                    full_ob = client.get_full_orderbook(market_id)
                    if full_ob is None:
                        continue
                    yes_price = full_ob['yes_ask']
                    no_price = full_ob['no_ask']
                else:
                    # 快速模式：只获取 Yes 价格，No 用 fallback
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
                logger.debug(f"解析 Predict 市场失败: {e}")
                continue

        logger.info(f"Predict: fetched {len(parsed)} markets (详细价格: 前{max_detailed_fetch}个)")
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

    # 关键修复：为每个市场添加纯文本标题用于匹配
    # title_with_html 保留用于显示
    # title_plain 用于匹配
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

    # 使用统一匹配器（使用纯文本标题）
    matcher = MarketMatcher({})
    matched_pairs = matcher.match_markets_cross_platform(
        markets_a_plain, markets_b_plain,
        title_field_a='title_plain', title_field_b='title_plain',  # 使用纯文本
        id_field_a='id', id_field_b='id',
        platform_a=platform_a_name.lower(), platform_b=platform_b_name.lower(),
        min_similarity=0.50,  # v3 算法已有硬约束阻止假匹配，阈值不需要太高
    )

    logger.info(f"[{platform_a_name} vs {platform_b_name}] MarketMatcher 找到 {len(matched_pairs)} 对匹配")

    for ma, mb, confidence in matched_pairs:
        checked_pairs += 1

        # Check end date similarity (如果有的话)
        end_date_a = ma.get('end_date', '')
        end_date_b = mb.get('end_date', '')
        if end_date_a and end_date_b:
            try:
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
                'market': strip_html(ma['title_with_html']),  # Strip HTML for market name
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
                'market': strip_html(mb['title_with_html']),  # Strip HTML for market name
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
