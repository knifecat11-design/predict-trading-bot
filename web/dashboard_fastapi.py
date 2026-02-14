"""
Cross-platform prediction market arbitrage dashboard - FastAPI + WebSocket
Platforms: Polymarket, Opinion.trade, Predict.fun
升级到 FastAPI 以支持 WebSocket 实时价格推送
"""

import os
import sys
import json
import time
import logging
import asyncio
import traceback
from datetime import datetime
from typing import Dict, List, Set, Optional
from dataclasses import dataclass, field

# FastAPI imports
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketState
import uvicorn

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

# ==================== FastAPI App ====================
app = FastAPI(title="Prediction Market Arbitrage Dashboard")

# ==================== Global State ====================
@dataclass
class DashboardState:
    """Dashboard 状态（线程安全）"""
    platforms: Dict[str, Dict] = field(default_factory=dict)
    arbitrage: List[Dict] = field(default_factory=list)
    scan_count: int = 0
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    threshold: float = 2.0
    last_scan: str = '-'
    error: Optional[str] = None
    # 实时价格缓存
    realtime_prices: Dict[str, Dict] = field(default_factory=dict)

    def get_platforms_copy(self) -> Dict:
        """获取平台数据副本"""
        return {k: v.copy() for k, v in self.platforms.items()}

    def get_arbitrage_copy(self) -> List:
        """获取套利列表副本"""
        return self.arbitrage.copy()


_state = DashboardState(
    platforms={
        'polymarket': {'status': 'unknown', 'markets': [], 'last_update': 0, 'count': 0},
        'opinion': {'status': 'unknown', 'markets': [], 'last_update': 0, 'count': 0},
        'predict': {'status': 'unknown', 'markets': [], 'last_update': 0, 'count': 0},
    }
)

# WebSocket 连接管理
class ConnectionManager:
    """WebSocket 连接管理器"""

    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        """接受新连接"""
        await self.accept(websocket)
        async with self._lock:
            self.active_connections.append(websocket)
        logger.info(f"WebSocket 连接: {len(self.active_connections)} 个活跃连接")

    async def accept(self, websocket: WebSocket):
        """接受 WebSocket 连接"""
        await websocket.accept()

    async def disconnect(self, websocket: WebSocket):
        """断开连接"""
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.info(f"WebSocket 断开: {len(self.active_connections)} 个活跃连接")

    async def broadcast_json(self, message: dict):
        """广播 JSON 消息到所有连接"""
        if not self.active_connections:
            return

        disconnected = []
        async with self._lock:
            for connection in self.active_connections:
                try:
                    if connection.client_state == WebSocketState.CONNECTED:
                        await connection.send_json(message)
                except Exception as e:
                    logger.debug(f"广播失败: {e}")
                    disconnected.append(connection)

        # 清理断开的连接
        for conn in disconnected:
            await self.disconnect(conn)

    async def send_personal_json(self, message: dict, websocket: WebSocket):
        """发送消息到特定连接"""
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_json(message)
        except Exception as e:
            logger.debug(f"发送个人消息失败: {e}")

    def get_connection_count(self) -> int:
        """获取活跃连接数"""
        return len(self.active_connections)


manager = ConnectionManager()


# ==================== Helper Functions (从 dashboard.py 复制）====================
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
        'will', 'won', 'would', 'the', 'a', 'an',
        'there', 'this', 'that', 'have', 'has', 'had',
        'be', 'been', 'being', 'for', 'from', 'with',
        'about', 'against', 'between', 'into', 'through', 'during',
        'before', 'after', 'above', 'below', 'over', 'under', 'again',
        'off', 'more', 'as', 'is', 'are', 'was', 'were',
        'when', 'where', 'while', 'how', 'what', 'which',
        'who', 'whom', 'whose', 'why', 'whether', 'if',
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


# 导入数据获取函数（从 dashboard.py 复制）
def fetch_polymarket_data(config):
    """Fetch Polymarket markets"""
    try:
        from src.polymarket_api import PolymarketClient
        poly_client = PolymarketClient(config)
        markets = poly_client.get_all_tags_markets(limit_per_tag=500)

        parsed = []
        for m in markets[:5000]:
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
                        import json
                        prices = json.loads(outcome_str)
                    else:
                        prices = outcome_str
                    if not prices or len(prices) < 2:
                        continue
                    yes_price = float(prices[0])
                    no_price = float(prices[1])

                if yes_price <= 0 or no_price <= 0:
                    continue

                market_slug = m.get('slug', '')
                if not market_slug:
                    events = m.get('events', [])
                    market_slug = events[0].get('slug', '') if events else condition_id

                parsed.append({
                    'id': condition_id,
                    'title': f"<a href='https://polymarket.com/event/{market_slug}' target='_blank' style='color:#03a9f4;font-weight:600'>{m.get('question', '')[:80]}</a>",
                    'url': f"https://polymarket.com/event/{market_slug}",
                    'yes': round(yes_price, 4),
                    'no': round(no_price, 4),
                    'amount': None,
                    'volume': float(m.get('volume24hr', 0) or 0),
                    'liquidity': float(m.get('liquidity', 0) or 0),
                    'platform': 'polymarket',
                    'end_date': m.get('endDate', ''),
                })
            except Exception as e:
                logger.debug(f"解析 Polymarket 市场失败: {e}")
                continue

        logger.info(f"Polymarket: fetched {len(parsed)} markets")
        return 'active', parsed
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
        from src.opinion_api import OpinionAPIClient
        client = OpinionAPIClient(config)
        raw_markets = client.get_markets(status='activated', sort_by=5, limit=500)

        if not raw_markets:
            return 'error', []

        logger.info(f"Opinion: 获取到 {len(raw_markets)} 个原始市场，开始解析价格...")

        parsed = []
        for idx, m in enumerate(raw_markets[:500]):
            try:
                market_id = str(m.get('marketId', ''))
                title = m.get('marketTitle', '')
                yes_token = m.get('yesTokenId', '')
                no_token = m.get('noTokenId', '')

                yes_price = client.get_token_price(yes_token)
                if yes_price is None:
                    continue

                no_price = None
                if no_token and no_token.strip():
                    no_price = client.get_token_price(no_token)

                if no_price is None:
                    no_price = round(1.0 - yes_price, 4)

                if yes_price <= 0 or yes_price >= 1 or no_price <= 0 or no_price >= 1:
                    continue

                parsed.append({
                    'id': market_id,
                    'title': f"<a href='https://app.opinion.trade/detail?topicId={market_id}' target='_blank' style='color:#d29922;font-weight:600'>{title[:80]}</a>",
                    'url': f"https://app.opinion.trade/detail?topicId={market_id}",
                    'yes': round(yes_price, 4),
                    'no': round(no_price, 4),
                    'amount': None,
                    'volume': float(m.get('volume24h', m.get('volume', 0)) or 0),
                    'liquidity': 0,
                    'platform': 'opinion',
                    'end_date': m.get('cutoff_at', ''),
                })
            except Exception as e:
                logger.debug(f"解析 Opinion 市场失败: {e}")
                continue

            if len(parsed) >= 500:
                break

        logger.info(f"Opinion: fetched {len(parsed)} markets")
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
        raw_markets = client.get_markets(status='open', limit=100)

        if not raw_markets:
            return 'error', []

        parse_limit = 100
        parsed = []
        skipped_no_orderbook = 0

        for idx, m in enumerate(raw_markets[:parse_limit]):
            try:
                market_id = m.get('id', m.get('market_id', ''))
                if not market_id:
                    continue

                full_ob = client.get_full_orderbook(market_id, use_cache=True)
                if full_ob is None:
                    skipped_no_orderbook += 1
                    continue

                yes_price = full_ob['yes_ask']
                no_price = full_ob['no_ask']
                ask_size = full_ob.get('ask_size', 0)

                question_text = (m.get('question') or m.get('title', ''))
                market_slug = slugify(question_text)
                parsed.append({
                    'id': market_id,
                    'title': f"<a href='https://predict.fun/market/{market_slug}' target='_blank' style='color:#9c27b0;font-weight:600'>{question_text[:80]}</a>",
                    'url': f"https://predict.fun/market/{market_slug}",
                    'yes': round(yes_price, 4),
                    'no': round(no_price, 4),
                    'amount': ask_size,
                    'volume': 0,
                    'liquidity': 0,
                    'platform': 'predict',
                    'end_date': '',
                })

                if (idx + 1) % 10 == 0:
                    logger.info(f"Predict: 已解析 {idx + 1}/{min(parse_limit, len(raw_markets))} 个市场...")

            except Exception as e:
                logger.debug(f"解析 Predict 市场失败: {e}")
                continue

        logger.info(f"Predict: fetched {len(parsed)} markets (跳过无订单簿: {skipped_no_orderbook})")
        return 'active', parsed
    except Exception as e:
        logger.error(f"Predict fetch error: {e}")
        return 'error', []


def find_cross_platform_arbitrage(markets_a, markets_b, platform_a_name, platform_b_name, threshold=2.0):
    """Find arbitrage between two platform market lists"""
    from src.market_matcher import MarketMatcher

    opportunities = []
    checked_pairs = 0

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

    logger.info(f"[{platform_a_name} vs {platform_b_name}] MarketMatcher 找到 {len(matched_pairs)} 对匹配")

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
                    continue
            except Exception as e:
                logger.debug(f"End date parsing failed: {e}")

        combined1 = ma['yes'] + mb['no']
        arb1 = (1.0 - combined1) * 100

        combined2 = mb['yes'] + ma['no']
        arb2 = (1.0 - combined2) * 100

        market_key_base = f"{platform_a_name}-{platform_b_name}-{ma.get('id','')}-{mb.get('id','')}"

        amount_a = ma.get('amount')
        amount_b = mb.get('amount')
        min_amount = None
        if amount_a is not None and amount_b is not None:
            min_amount = min(amount_a, amount_b)
        elif amount_a is not None:
            min_amount = amount_a
        elif amount_b is not None:
            min_amount = amount_b

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
                'arbitrage': round(arb1, 2),
                'amount': min_amount,
                'confidence': round(confidence, 2),
                'timestamp': datetime.now().strftime('%H:%M:%S'),
                'market_key': f"{market_key_base}-yes1_no2",
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
                'arbitrage': round(arb2, 2),
                'amount': min_amount,
                'confidence': round(confidence, 2),
                'timestamp': datetime.now().strftime('%H:%M:%S'),
                'market_key': f"{market_key_base}-yes2_no1",
            })

    opportunities.sort(key=lambda x: x['arbitrage'], reverse=True)
    logger.info(f"[{platform_a_name} vs {platform_b_name}] Checked: {checked_pairs}, Found: {len(opportunities)} opportunities")

    return opportunities


# ==================== FastAPI Routes ====================
@app.get("/", response_class=HTMLResponse)
async def index():
    """主页 - 返回 HTML dashboard"""
    try:
        template_path = os.path.join(os.path.dirname(__file__), 'templates', 'index.html')
        if os.path.exists(template_path):
            with open(template_path, 'r', encoding='utf-8') as f:
                return HTMLResponse(content=f.read())
        else:
            return HTMLResponse(content="<h1>Dashboard template not found</h1><p>Please create web/templates/index.html</p>")
    except Exception as e:
        logger.error(f"Template error: {e}")
        return HTMLResponse(content=f"<h1>Dashboard Error</h1><p>{e}</p>")


@app.get("/api/state")
async def api_state():
    """API 端点：获取当前状态"""
    return JSONResponse(content={
        'platforms': _state.get_platforms_copy(),
        'arbitrage': _state.get_arbitrage_copy(),
        'scan_count': _state.scan_count,
        'started_at': _state.started_at,
        'threshold': _state.threshold,
        'last_scan': _state.last_scan,
        'error': _state.error,
    })


@app.get("/health")
async def health():
    """健康检查"""
    return JSONResponse(content={'status': 'ok', 'timestamp': datetime.now().isoformat()})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket 端点：实时推送价格更新和套利机会

    客户端可以订阅：
    - prices: 实时价格更新
    - arbitrage: 套利机会更新
    - scan: 扫描状态更新
    """
    await manager.connect(websocket)
    client_id = id(websocket)

    try:
        # 发送初始状态
        await websocket.send_json({
            "type": "connected",
            "message": "WebSocket connected successfully",
            "client_id": client_id
        })

        # 监听客户端消息
        while True:
            data = await websocket.receive_json()

            # 处理订阅请求
            if data.get('type') == 'subscribe':
                channels = data.get('channels', [])
                logger.debug(f"Client {client_id} subscribed to: {channels}")

                # 发送确认
                await websocket.send_json({
                    "type": "subscribed",
                    "channels": channels
                })

    except WebSocketDisconnect:
        logger.info(f"Client {client_id} disconnected")
        await manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await manager.disconnect(websocket)


# ==================== Background Scanner ====================
async def background_scanner():
    """
    后台扫描器（异步版本）
    定期扫描所有平台并更新状态
    """
    config = load_config()
    threshold = float(config.get('opinion_poly', {}).get('min_arbitrage_threshold', 2.0))
    scan_interval = int(config.get('arbitrage', {}).get('scan_interval', 30))

    logger.info(f"Scanner started: threshold={threshold}%, interval={scan_interval}s")

    while True:
        try:
            now = time.time()

            # 并行获取所有平台数据
            poly_status, poly_markets = fetch_polymarket_data(config)
            _state.platforms['polymarket'] = {
                'status': poly_status,
                'markets': poly_markets[:20],
                'count': len(poly_markets),
                'last_update': now,
            }
            logger.info(f"[Polymarket] 已更新: {len(poly_markets)} 个市场, status={poly_status}")

            opinion_status, opinion_markets = fetch_opinion_data(config)
            _state.platforms['opinion'] = {
                'status': opinion_status,
                'markets': opinion_markets[:20],
                'count': len(opinion_markets),
                'last_update': now,
            }
            logger.info(f"[Opinion] 已更新: {len(opinion_markets)} 个市场, status={opinion_status}")

            predict_status, predict_markets = fetch_predict_data(config)
            _state.platforms['predict'] = {
                'status': predict_status,
                'markets': predict_markets[:20],
                'count': len(predict_markets),
                'last_update': now,
            }
            logger.info(f"[Predict] 已更新: {len(predict_markets)} 个市场, status={predict_status}")

            # 查找套利机会
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

            # 更新状态
            existing_arb = _state.get_arbitrage_copy()
            new_arb_map = {opp['market_key']: opp for opp in all_arb if opp.get('market_key')}
            old_arb_map = {opp['market_key']: opp for opp in existing_arb if opp.get('market_key')}

            for key, old_opp in old_arb_map.items():
                if key not in new_arb_map:
                    new_arb_map[key] = old_opp

            for key, new_opp in new_arb_map.items():
                if key in old_arb_map:
                    old_opp = old_arb_map[key]
                    price_change = abs(new_opp['arbitrage'] - old_opp['arbitrage'])
                    if price_change < 0.5:
                        new_arb_map[key] = old_opp
                    new_opp['timestamp'] = datetime.now().strftime('%H:%M:%S')

            _state.arbitrage = list(new_arb_map.values())[:50]
            _state.scan_count += 1
            _state.last_scan = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            _state.threshold = threshold
            _state.error = None

            # WebSocket 推送：扫描完成
            await manager.broadcast_json({
                "type": "scan_complete",
                "data": {
                    "scan_count": _state.scan_count,
                    "last_scan": _state.last_scan,
                    "arbitrage_count": len(_state.arbitrage),
                    "platforms": {
                        "polymarket": _state.platforms['polymarket']['count'],
                        "opinion": _state.platforms['opinion']['count'],
                        "predict": _state.platforms['predict']['count'],
                    }
                }
            })

            logger.info(
                f"Scan #{_state.scan_count}: "
                f"Poly={len(poly_markets)} Opinion={len(opinion_markets)} "
                f"Predict={len(predict_markets)} Arb={len(all_arb)}"
            )

        except Exception as e:
            logger.error(f"Scanner error: {e}")
            logger.error(traceback.format_exc())
            _state.error = str(e)

            # WebSocket 推送：错误
            await manager.broadcast_json({
                "type": "error",
                "data": {
                    "error": str(e),
                    "timestamp": datetime.now().isoformat()
                }
            })

        await asyncio.sleep(scan_interval)


# ==================== Main ====================
def main():
    logger.info("=" * 60)
    logger.info("  Prediction Market Arbitrage Dashboard - FastAPI + WebSocket")
    logger.info("=" * 60)

    port = int(os.getenv('PORT', 8000))
    logger.info(f"Dashboard starting on http://0.0.0.0:{port}")

    # 启动后台扫描器
    scanner_task = None

    async def lifespan(app):
        """应用生命周期管理"""
        # 启动时：启动后台扫描器
        nonlocal scanner_task
        scanner_task = asyncio.create_task(background_scanner())
        logger.info("Background scanner started")

        yield

        # 关闭时：取消后台任务
        if scanner_task:
            scanner_task.cancel()
            try:
                await scanner_task
            except asyncio.CancelledError:
                pass
            logger.info("Background scanner stopped")

    # 使用 uvicorn 运行
    config = uvicorn.Config(
        app=app,
        host='0.0.0.0',
        port=port,
        log_level="info"
    )
    server = uvicorn.Server(config)

    # 启动服务器
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # 启动后台扫描器
    loop.create_task(background_scanner())
    logger.info("Background scanner started")

    # 运行服务器
    server.run()


if __name__ == '__main__':
    main()
