"""
Polymarket WebSocket 实时监控客户端
基于 py-clob_client 实现 6 个并行连接监控 1500 个市场
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional, Callable, Set
from dataclasses import dataclass
from datetime import datetime, timedelta
import websockets
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class WebSocketConfig:
    """WebSocket 配置"""
    ws_url: str = "wss://clob.polymarket.com/ws"
    num_connections: int = 6  # 并行连接数
    markets_per_connection: int = 250  # 每个连接监控的市场数
    reconnect_delay: int = 5  # 重连延迟（秒）
    max_reconnect_attempts: int = 10  # 最大重连次数
    ping_interval: int = 30  # 心跳间隔（秒）


@dataclass
class MarketFilter:
    """市场过滤器"""
    min_liquidity_usd: float = 10000  # 最小流动性（美元）
    max_days_until_resolution: int = 7  # 最大结算天数
    min_price: float = 0.01  # 最小价格
    max_price: float = 0.99  # 最大价格


@dataclass
class MarketPrice:
    """市场价格更新"""
    token_id: str
    market_id: str
    price: float
    timestamp: float
    side: str  # 'yes' or 'no'


class PolymarketWebSocketClient:
    """
    Polymarket WebSocket 客户端
    使用多个并行连接监控大量市场
    """

    def __init__(self, config: WebSocketConfig, market_filter: MarketFilter):
        self.config = config
        self.filter = market_filter
        self.logger = logging.getLogger(__name__)

        # WebSocket 连接
        self._connections: List[websockets.WebSocketClientProtocol] = []
        self._running = False

        # 市场分配
        self._market_assignments: Dict[int, Set[str]] = defaultdict(set)  # {connection_id: {token_ids}}
        self._token_to_market: Dict[str, str] = {}  # {token_id: market_id}

        # 价格缓存
        self._price_cache: Dict[str, MarketPrice] = {}  # {token_id: MarketPrice}
        self._market_prices: Dict[str, Dict[str, float]] = defaultdict(dict)  # {market_id: {'yes': price, 'no': price}}

        # 回调函数
        self._on_price_update: Optional[Callable[[MarketPrice], None]] = None
        self._on_market_update: Optional[Callable[[str, Dict[str, float]], None]] = None

        # 统计信息
        self._stats = {
            'messages_received': 0,
            'price_updates': 0,
            'connections_active': 0,
            'reconnect_count': 0
        }

    async def connect(self, markets: List[Dict]):
        """
        连接到 WebSocket 并订阅市场

        Args:
            markets: 市场列表，每个市场包含 token_id, market_id 等信息
        """
        self.logger.info(f"正在连接到 Polymarket WebSocket ({len(markets)} 个市场)...")

        # 过滤市场
        filtered_markets = self._filter_markets(markets)
        self.logger.info(f"过滤后剩余 {len(filtered_markets)} 个市场")

        if not filtered_markets:
            self.logger.warning("没有符合条件的市场")
            return

        # 分配市场到各个连接
        self._assign_markets(filtered_markets)

        # 创建并行连接
        self._running = True
        tasks = []
        for conn_id in range(self.config.num_connections):
            task = asyncio.create_task(self._run_connection(conn_id))
            tasks.append(task)

        # 等待所有连接
        await asyncio.gather(*tasks, return_exceptions=True)

    def _filter_markets(self, markets: List[Dict]) -> List[Dict]:
        """过滤市场"""
        filtered = []
        now = datetime.now()

        for market in markets:
            # 检查流动性
            volume = float(market.get('volume', 0))
            if volume < self.filter.min_liquidity_usd:
                continue

            # 检查结算时间
            end_date = market.get('end_date')
            if end_date:
                try:
                    end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                    days_until = (end_dt - now).days
                    if days_until > self.filter.max_days_until_resolution:
                        continue
                except:
                    pass

            # 检查价格范围
            yes_price = float(market.get('yes_price', 0.5))
            no_price = float(market.get('no_price', 0.5))
            if not (self.filter.min_price <= yes_price <= self.filter.max_price):
                continue

            filtered.append(market)

        return filtered

    def _assign_markets(self, markets: List[Dict]):
        """分配市场到各个连接"""
        self.logger.info(f"分配 {len(markets)} 个市场到 {self.config.num_connections} 个连接...")

        for i, market in enumerate(markets):
            conn_id = i % self.config.num_connections

            # 获取 token IDs
            market_id = market.get('condition_id') or market.get('question_id', '')
            token_id = market.get('token_id') or market.get('condition_id', '')

            if token_id:
                self._market_assignments[conn_id].add(token_id)
                self._token_to_market[token_id] = market_id

        # 记录分配结果
        for conn_id, tokens in self._market_assignments.items():
            self.logger.info(f"  连接 {conn_id}: {len(tokens)} 个市场")

    async def _run_connection(self, conn_id: int):
        """运行单个 WebSocket 连接"""
        tokens = self._market_assignments.get(conn_id, set())

        if not tokens:
            self.logger.warning(f"连接 {conn_id}: 没有分配市场")
            return

        reconnect_attempts = 0

        while self._running and reconnect_attempts < self.config.max_reconnect_attempts:
            try:
                self.logger.info(f"连接 {conn_id}: 正在连接到 WebSocket...")

                async with websockets.connect(
                    self.config.ws_url,
                    ping_interval=self.config.ping_interval
                ) as ws:
                    self._connections.append(ws)
                    self._stats['connections_active'] += 1

                    self.logger.info(f"连接 {conn_id}: 已连接，正在订阅 {len(tokens)} 个市场...")

                    # 订阅所有市场
                    for token_id in tokens:
                        await self._subscribe_market(ws, token_id)

                    self.logger.info(f"连接 {conn_id}: 订阅完成，开始接收数据...")

                    # 重置重连计数
                    reconnect_attempts = 0

                    # 接收消息
                    async for message in ws:
                        await self._handle_message(message, conn_id)

            except websockets.exceptions.ConnectionClosed as e:
                self.logger.error(f"连接 {conn_id}: 连接断开: {e}")
                reconnect_attempts += 1
                self._stats['reconnect_count'] += 1

                if reconnect_attempts < self.config.max_reconnect_attempts:
                    self.logger.info(f"连接 {conn_id}: {self.config.reconnect_delay} 秒后重连...")
                    await asyncio.sleep(self.config.reconnect_delay)

            except Exception as e:
                self.logger.error(f"连接 {conn_id}: 错误: {e}")
                reconnect_attempts += 1

                if reconnect_attempts < self.config.max_reconnect_attempts:
                    await asyncio.sleep(self.config.reconnect_delay)

        self.logger.error(f"连接 {conn_id}: 已停止（重连次数超限）")

    async def _subscribe_market(self, ws, token_id: str):
        """订阅市场价格更新"""
        # 订阅 YES token
        subscribe_msg = {
            "type": "subscribe",
            "channel": f"price_level::{token_id}_YES"
        }
        await ws.send(json.dumps(subscribe_msg))

        # 订阅 NO token
        subscribe_msg = {
            "type": "subscribe",
            "channel": f"price_level::{token_id}_NO"
        }
        await ws.send(json.dumps(subscribe_msg))

    async def _handle_message(self, message: str, conn_id: int):
        """处理 WebSocket 消息"""
        try:
            data = json.loads(message)
            self._stats['messages_received'] += 1

            # 处理价格更新
            if data.get('type') == 'price_level':
                await self._process_price_update(data)

        except json.JSONDecodeError:
            self.logger.warning(f"连接 {conn_id}: 无效的 JSON 消息")
        except Exception as e:
            self.logger.error(f"连接 {conn_id}: 处理消息错误: {e}")

    async def _process_price_update(self, data: Dict):
        """处理价格更新"""
        try:
            # 解析 token_id 和价格
            channel = data.get('channel', '')
            price_data = data.get('payload', {})

            if not price_data:
                return

            # 从 channel 解析 token_id 和 side
            # 格式: price_level::{token_id}_YES 或 price_level::{token_id}_NO
            parts = channel.split('::')
            if len(parts) < 2:
                return

            token_side = parts[1]
            if '_' in token_side:
                token_id, side = token_side.rsplit('_', 1)
            else:
                return

            # 获取价格（取最新成交价）
            price = float(price_data.get('price', 0))
            if price == 0:
                return

            # 获取 market_id
            market_id = self._token_to_market.get(token_id)
            if not market_id:
                return

            # 创建价格对象
            price_update = MarketPrice(
                token_id=token_id,
                market_id=market_id,
                price=price,
                timestamp=datetime.now().timestamp(),
                side=side.lower()
            )

            # 更新缓存
            self._price_cache[token_id] = price_update
            self._market_prices[market_id][side.lower()] = price
            self._stats['price_updates'] += 1

            # 触发回调
            if self._on_price_update:
                await self._safe_callback(self._on_price_update, price_update)

            if self._on_market_update:
                await self._safe_callback(
                    self._on_market_update,
                    market_id,
                    self._market_prices[market_id]
                )

        except Exception as e:
            self.logger.error(f"处理价格更新错误: {e}")

    async def _safe_callback(self, callback, *args):
        """安全执行回调函数"""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(*args)
            else:
                callback(*args)
        except Exception as e:
            self.logger.error(f"回调函数错误: {e}")

    def on_price_update(self, callback: Callable[[MarketPrice], None]):
        """注册价格更新回调"""
        self._on_price_update = callback

    def on_market_update(self, callback: Callable[[str, Dict[str, float]], None]):
        """注册市场更新回调"""
        self._on_market_update = callback

    def get_price(self, token_id: str) -> Optional[MarketPrice]:
        """获取 token 价格"""
        return self._price_cache.get(token_id)

    def get_market_prices(self, market_id: str) -> Optional[Dict[str, float]]:
        """获取市场价格（yes 和 no）"""
        prices = self._market_prices.get(market_id)
        if prices and 'yes' in prices and 'no' in prices:
            return prices
        return None

    def get_statistics(self) -> Dict:
        """获取统计信息"""
        return {
            **self._stats,
            'markets_monitored': len(self._token_to_market),
            'price_cache_size': len(self._price_cache),
            'connections_active': len(self._connections)
        }

    async def disconnect(self):
        """断开所有连接"""
        self.logger.info("正在断开 WebSocket 连接...")
        self._running = False

        for ws in self._connections:
            await ws.close()

        self._connections.clear()
        self.logger.info("所有连接已断开")

    def is_connected(self) -> bool:
        """检查是否已连接"""
        return len(self._connections) > 0


def create_websocket_client(
    num_connections: int = 6,
    markets_per_connection: int = 250,
    min_liquidity: float = 10000,
    max_days: int = 7
) -> PolymarketWebSocketClient:
    """
    创建 WebSocket 客户端

    Args:
        num_connections: 并行连接数
        markets_per_connection: 每个连接监控的市场数
        min_liquidity: 最小流动性（美元）
        max_days: 最大结算天数

    Returns:
        PolymarketWebSocketClient 实例
    """
    config = WebSocketConfig(
        num_connections=num_connections,
        markets_per_connection=markets_per_connection
    )

    market_filter = MarketFilter(
        min_liquidity_usd=min_liquidity,
        max_days_until_resolution=max_days
    )

    return PolymarketWebSocketClient(config, market_filter)
