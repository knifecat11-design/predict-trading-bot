"""
Polymarket WebSocket 客户端 - 实时价格监控
实现基于 websockets 库，支持并行连接和市场订阅

API 文档: https://docs.polymarket.com
版本: v1.0 (2026-02-14)
"""

import asyncio
import json
import logging
import time
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from collections import defaultdict

import websockets
from websockets.exceptions import ConnectionClosed, ConnectionError

logger = logging.getLogger(__name__)


@dataclass
class PriceUpdate:
    """价格更新"""
    token_id: str
    price: float
    side: str  # 'YES' or 'NO'
    timestamp: float


@dataclass
class MarketPrice:
    """市场完整价格（Yes + No）"""
    market_id: str
    yes_price: Optional[float] = None
    no_price: Optional[float] = None
    yes_bid: Optional[float] = None
    yes_ask: Optional[float] = None
    no_bid: Optional[float] = None
    no_ask: Optional[float] = None
    timestamp: float = 0.0

    @property
    def is_complete(self) -> bool:
        """是否同时有 Yes 和 No 价格"""
        return self.yes_price is not None and self.no_price is not None

    @property
    def spread(self) -> Optional[float]:
        """Yes + No 总价（用于检测套利）"""
        if self.is_complete:
            return self.yes_price + self.no_price
        return None


@dataclass
class WebSocketConfig:
    """WebSocket 配置"""
    num_connections: int = 6
    markets_per_connection: int = 250
    min_liquidity: int = 10000  # 最小流动性（美元）
    max_days: int = 7  # 最大结算天数
    reconnect_delay: int = 5  # 重连延迟（秒）
    max_reconnect_attempts: int = 10  # 最大重连次数
    ping_interval: int = 30  # 心跳间隔（秒）
    ws_url: str = "wss://api.polymarket.com/ws"


class PolymarketWebSocketClient:
    """
    Polymarket WebSocket 客户端

    特性：
    - 并行连接（6 个 WebSocket 连接）
    - 智能市场分配（每个连接 250 个市场）
    - 自动重连机制
    - 价格更新回调
    """

    WS_URL = "wss://api.polymarket.com/ws"

    def __init__(self, config: WebSocketConfig):
        self.config = config

        # 连接管理
        self._connections: List[websockets.WebSocketClientProtocol] = []
        self._running = False
        self._tasks: List[asyncio.Task] = []

        # 市场数据
        self._market_prices: Dict[str, MarketPrice] = {}
        self._token_to_market: Dict[str, str] = {}  # token_id -> market_id 映射

        # 回调函数
        self._on_price_update: Optional[Callable[[PriceUpdate], None]] = None
        self._on_market_update: Optional[Callable[[str, Dict], None]] = None
        self._on_connection_status: Optional[Callable[[str, bool], None]] = None

        # 统计
        self._stats = {
            'messages_received': 0,
            'price_updates': 0,
            'connections_active': 0,
            'markets_monitored': 0,
            'start_time': 0
        }

    def on_price_update(self, callback: Callable[[PriceUpdate], None]):
        """注册价格更新回调"""
        self._on_price_update = callback

    def on_market_update(self, callback: Callable[[str, Dict], None]):
        """注册市场更新回调（完整的 Yes + No 价格）"""
        self._on_market_update = callback

    def on_connection_status(self, callback: Callable[[str, bool], None]):
        """注册连接状态回调"""
        self._on_connection_status = callback

    async def connect(self, markets: List[Dict]):
        """
        连接 WebSocket 并订阅市场

        Args:
            markets: 市场列表，每个市场包含:
                - condition_id (str): 市场 ID
                - token_id (str): Yes token ID
                - token_id_no (str): No token ID (可选)
                - liquidity (float): 流动性
                - end_date (str): 结算日期
        """
        logger.info(f"正在连接 Polymarket WebSocket ({len(markets)} 个市场)...")

        # 过滤市场
        filtered_markets = self._filter_markets(markets)
        logger.info(f"过滤后: {len(filtered_markets)} 个市场")
        self._stats['markets_monitored'] = len(filtered_markets)

        if not filtered_markets:
            logger.warning("没有符合条件的市场")
            return False

        # 分配市场到连接
        market_chunks = self._allocate_markets(filtered_markets)
        logger.info(f"分配到 {len(market_chunks)} 个连接")

        # 启动连接
        self._running = True
        self._stats['start_time'] = time.time()

        for i, chunk in enumerate(market_chunks):
            task = asyncio.create_task(self._run_connection(i, chunk))
            self._tasks.append(task)

        logger.info("✓ WebSocket 连接已启动")
        return True

    async def disconnect(self):
        """断开所有连接"""
        logger.info("正在断开 WebSocket 连接...")
        self._running = False

        # 取消所有任务
        for task in self._tasks:
            if not task.done():
                task.cancel()

        # 等待任务完成
        await asyncio.gather(*self._tasks, return_exceptions=True)

        logger.info("✓ WebSocket 已断开")

    def _filter_markets(self, markets: List[Dict]) -> List[Dict]:
        """过滤市场（流动性、结算时间）"""
        filtered = []

        for market in markets:
            try:
                # 检查流动性
                liquidity = float(market.get('liquidity', 0) or 0)
                if liquidity < self.config.min_liquidity:
                    continue

                # 检查结算时间（如果有）
                end_date = market.get('end_date', '')
                if end_date and self.config.max_days:
                    # 简单检查：如果是 ISO 格式日期
                    try:
                        from datetime import datetime
                        end = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                        days_left = (end - datetime.now()).days
                        if days_left > self.config.max_days:
                            continue
                    except:
                        pass  # 如果日期解析失败，不过滤

                filtered.append(market)
            except Exception as e:
                logger.debug(f"过滤市场失败: {e}")
                continue

        return filtered

    def _allocate_markets(self, markets: List[Dict]) -> List[List[Dict]]:
        """分配市场到多个连接"""
        chunks = []
        for i in range(0, len(markets), self.config.markets_per_connection):
            chunk = markets[i:i + self.config.markets_per_connection]
            chunks.append(chunk)
        return chunks

    async def _run_connection(self, conn_id: int, markets: List[Dict]):
        """运行单个 WebSocket 连接"""
        url = self.config.ws_url
        reconnect_attempts = 0

        while self._running and reconnect_attempts < self.config.max_reconnect_attempts:
            try:
                logger.info(f"[连接 {conn_id}] 正在连接...")
                async with websockets.connect(url, ping_interval=self.config.ping_interval) as ws:
                    self._connections.append(ws)
                    self._stats['connections_active'] = len(self._connections)

                    # 通知连接状态
                    if self._on_connection_status:
                        self._on_connection_status(f"conn_{conn_id}", True)

                    logger.info(f"[连接 {conn_id}] ✓ 已连接，订阅 {len(markets)} 个市场")

                    # 订阅市场
                    for market in markets:
                        await self._subscribe_market(ws, market)

                    # 接收消息
                    async for message in ws:
                        if not self._running:
                            break

                        self._stats['messages_received'] += 1
                        await self._handle_message(message)

                    # 连接正常关闭
                    logger.info(f"[连接 {conn_id}] 已关闭")
                    break

            except (ConnectionClosed, ConnectionError) as e:
                logger.warning(f"[连接 {conn_id}] 连接断开: {e}")
                if self._on_connection_status:
                    self._on_connection_status(f"conn_{conn_id}", False)
                self._stats['connections_active'] = max(0, self._stats['connections_active'] - 1)

                reconnect_attempts += 1
                if reconnect_attempts < self.config.max_reconnect_attempts:
                    logger.info(f"[连接 {conn_id}] {self.config.reconnect_delay} 秒后重连...")
                    await asyncio.sleep(self.config.reconnect_delay)
                else:
                    logger.error(f"[连接 {conn_id}] 达到最大重连次数")

            except Exception as e:
                logger.error(f"[连接 {conn_id}] 错误: {e}")
                break

    async def _subscribe_market(self, ws: websockets.WebSocketClientProtocol, market: Dict):
        """订阅单个市场"""
        try:
            condition_id = market.get('conditionId') or market.get('condition_id')
            if not condition_id:
                return

            # 初始化市场价格
            self._market_prices[condition_id] = MarketPrice(
                market_id=condition_id,
                timestamp=time.time()
            )

            # 订阅 Yes token
            # Polymarket WebSocket 格式: price_level::{token_id}_YES
            subscribe_msg = {
                "type": "subscribe",
                "channel": f"price_level::{condition_id}_YES"
            }
            await ws.send(json.dumps(subscribe_msg))

            # 订阅 No token (如果有 token_id_no)
            token_id_no = market.get('token_id_no')
            if token_id_no:
                subscribe_msg_no = {
                    "type": "subscribe",
                    "channel": f"price_level::{token_id_no}_NO"
                }
                await ws.send(json.dumps(subscribe_msg_no))

        except Exception as e:
            logger.debug(f"订阅市场失败: {e}")

    async def _handle_message(self, message: str):
        """处理 WebSocket 消息"""
        try:
            data = json.loads(message)

            # 价格更新消息
            if data.get('type') == 'price_level':
                await self._process_price_update(data)

        except Exception as e:
            logger.debug(f"处理消息失败: {e}")

    async def _process_price_update(self, data: Dict):
        """处理价格更新"""
        try:
            channel = data.get('channel', '')  # price_level::{token_id}_YES
            payload = data.get('payload', {})
            price = payload.get('price')

            if price is None:
                return

            # 解析 token_id 和 side
            parts = channel.split('::')
            if len(parts) < 2:
                return

            token_id_side = parts[1]  # {token_id}_YES or {token_id}_NO
            if '_' not in token_id_side:
                return

            token_id, side = token_id_side.rsplit('_', 1)
            price_float = float(price)

            # 更新市场价格
            market_id = token_id  # Polymarket token_id = condition_id

            if market_id not in self._market_prices:
                self._market_prices[market_id] = MarketPrice(
                    market_id=market_id,
                    timestamp=time.time()
                )

            market_price = self._market_prices[market_id]

            if side == 'YES':
                market_price.yes_price = price_float
                market_price.yes_bid = price_float
                market_price.yes_ask = price_float
            elif side == 'NO':
                market_price.no_price = price_float
                market_price.no_bid = price_float
                market_price.no_ask = price_float

            market_price.timestamp = time.time()

            # 回调：价格更新
            if self._on_price_update:
                update = PriceUpdate(
                    token_id=token_id,
                    price=price_float,
                    side=side,
                    timestamp=time.time()
                )
                try:
                    self._on_price_update(update)
                except Exception as e:
                    logger.debug(f"价格更新回调失败: {e}")

            # 回调：市场更新（Yes + No 都有）
            if market_price.is_complete and self._on_market_update:
                prices = {
                    'yes': market_price.yes_price,
                    'no': market_price.no_price,
                    'yes_bid': market_price.yes_bid,
                    'yes_ask': market_price.yes_ask,
                    'no_bid': market_price.no_bid,
                    'no_ask': market_price.no_ask,
                    'spread': market_price.spread,
                    'timestamp': market_price.timestamp
                }
                try:
                    self._on_market_update(market_id, prices)
                except Exception as e:
                    logger.debug(f"市场更新回调失败: {e}")

            self._stats['price_updates'] += 1

        except Exception as e:
            logger.debug(f"处理价格更新失败: {e}")

    def get_market_price(self, market_id: str) -> Optional[MarketPrice]:
        """获取市场实时价格"""
        return self._market_prices.get(market_id)

    def get_all_market_prices(self) -> Dict[str, MarketPrice]:
        """获取所有市场实时价格"""
        return self._market_prices.copy()

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        uptime = time.time() - self._stats['start_time'] if self._stats['start_time'] > 0 else 0
        return {
            'markets_monitored': self._stats['markets_monitored'],
            'messages_received': self._stats['messages_received'],
            'price_updates': self._stats['price_updates'],
            'connections_active': self._stats['connections_active'],
            'uptime_seconds': uptime,
            'markets_with_prices': len([m for m in self._market_prices.values() if m.is_complete])
        }


def create_websocket_client(
    num_connections: int = 6,
    markets_per_connection: int = 250,
    min_liquidity: int = 10000,
    max_days: int = 7
) -> PolymarketWebSocketClient:
    """
    创建 Polymarket WebSocket 客户端

    Args:
        num_connections: 并行连接数（默认 6）
        markets_per_connection: 每个连接的市场数（默认 250）
        min_liquidity: 最小流动性（默认 $10000）
        max_days: 最大结算天数（默认 7 天）

    Returns:
        WebSocket 客户端实例
    """
    config = WebSocketConfig(
        num_connections=num_connections,
        markets_per_connection=markets_per_connection,
        min_liquidity=min_liquidity,
        max_days=max_days
    )

    return PolymarketWebSocketClient(config)
