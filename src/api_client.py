"""
Predict.fun API 客户端 - 修复版本
修复了5个致命错误，专注于数据监控功能

API 文档: https://api.predict.fun/docs
版本: v3.0 (2026-02-12) - 修复所有认证和解析错误
"""

import time
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class MarketData:
    """市场数据"""
    market_id: str
    question: str
    current_price: float           # 中间价（仅用于显示）
    yes_bid: float                 # 买一价（实际可成交）
    yes_ask: float                 # 卖一价（实际可成交）
    no_bid: float                 # No token 买一价
    no_ask: float                 # No token 卖一价
    best_bid_size: float
    best_ask_size: float
    liquidity: float
    volume: float
    timestamp: float
    status: str                  # OPEN, REGISTERED, RESOLVED


@dataclass
class Order:
    """订单信息"""
    order_id: str
    side: str
    price: float
    size: float
    status: str
    timestamp: float


class PredictAPIClient:
    """
    Predict.fun API 客户端（修复版）

    修复的错误:
    1. 认证头: x-api-key (不是 Authorization: Bearer)
    2. 端点前缀: /v1/ (缺失导致404)
    3. 参数名: status=OPEN (大写), first (不是 limit)
    4. 响应解析: {"success": true, "cursor": "...", "data": [...]}
    5. 订单簿: 2D数组 [[price, size], ...] (不是 dict)
    """

    def __init__(self, config: Dict):
        self.config = config
        self.api_key = config.get('api', {}).get('api_key', '')
        self.base_url = config.get('api', {}).get('base_url', 'https://api.predict.fun')

        import requests
        self.session = requests.Session()

        # 修复1: 使用正确的认证头 x-api-key
        if self.api_key:
            self.session.headers.update({
                'x-api-key': self.api_key,  # 修复: 不是 'Authorization: Bearer'
                'Content-Type': 'application/json'
            })
            logger.info(f"Predict.fun API: {self.base_url} (已配置 x-api-key 认证)")
        else:
            logger.warning("未设置 PREDICT_API_KEY")

        # 缓存
        self._cache: List[Dict] = []
        self._cache_time = 0
        self._cache_duration = config.get('api', {}).get('cache_seconds', 30)

    def get_markets(self, status: str = 'OPEN', sort: str = None, limit: int = 100) -> List[Dict]:
        """
        获取市场列表（修复版）

        Args:
            status: 市场状态 (OPEN, REGISTERED, RESOLVED) - 必须大写
            sort: 排序方式（暂时禁用，API 不支持此参数）
            limit: 返回数量
        """
        try:
            # 检查缓存
            if time.time() - self._cache_time < self._cache_duration and self._cache:
                return self._cache[:limit]

            # 修复2: 添加 /v1/ 前缀
            # 修复3: 使用正确的参数名 (status=OPEN, first=limit)
            params = {
                'status': status.upper(),  # 必须大写: OPEN, REGISTERED, RESOLVED
                'first': min(limit, 100)  # 参数名是 first，不是 limit
            }
            # API 不支持 sort 参数，已移除
            response = self.session.get(
                f"{self.base_url}/v1/markets",  # 修复: 添加 /v1/ 前缀
                params=params,
                timeout=15
            )

            if response.status_code == 200:
                result = response.json()

                # 修复4: 正确解析游标分页响应
                if result.get('success') and 'data' in result:
                    markets = result['data']
                    if markets:
                        self._cache = markets
                        self._cache_time = time.time()
                        logger.info(f"Predict.fun: 获取到 {len(markets)} 个市场 (cursor={result.get('cursor', 'N/A')})")
                        return markets[:limit]
                elif not result.get('success'):
                    logger.error(f"Predict.fun API 错误: {result.get('message', 'Unknown error')}")
                else:
                    logger.warning(f"Predict.fun API 返回空数据")

            elif response.status_code == 401:
                logger.error("API 认证失败，请检查 PREDICT_API_KEY 或在网站上下单激活")
            elif response.status_code == 403:
                logger.error("API 权限不足，请在 Predict.fun 网站上下单激活 API Key")

            return self._cache[:limit] if self._cache else []

        except Exception as e:
            logger.error(f"获取市场失败: {e}")
            return self._cache if self._cache else []

    def _parse_price_level(self, level: list) -> Tuple[float, float]:
        """
        解析订单簿价格层级

        Args:
            level: [price, size] 格式的2D数组元素

        Returns:
            (price, size) 元组
        """
        if not level or len(level) < 2:
            return (None, 0)
        return (float(level[0]), float(level[1]))

    def _get_orderbook(self, market_id: str, outcome_id: int = 1) -> Dict:
        """
        获取订单簿（修复版）

        Args:
            market_id: 市场 ID (数字ID)
            outcome_id: 1=Yes token, 0=No token（注意：Predict.fun 可能不支持此参数）

        Returns:
            {'yes_bid': float, 'yes_ask': float, 'bid_size': float, 'ask_size': float}
        """
        try:
            # 修复2: 使用 /v1/ 前缀
            response = self.session.get(
                f"{self.base_url}/v1/markets/{market_id}/orderbook",
                timeout=10
            )

            if response.status_code == 200:
                result = response.json()

                # 修复4: 检查 success 字段
                if result.get('success') and 'data' in result:
                    data = result['data']

                    # 修复5: 订单簿是2D数组 [[price, size], ...]
                    asks = data.get('asks', []) or []
                    bids = data.get('bids', []) or []

                    if asks and bids:
                        # 解析第一个价格层级
                        yes_ask, ask_size = self._parse_price_level(asks[0])
                        yes_bid, bid_size = self._parse_price_level(bids[0])

                        if yes_ask is not None and yes_bid is not None:
                            return {
                                'yes_bid': round(yes_bid, 4),
                                'yes_ask': round(yes_ask, 4),
                                'bid_size': bid_size,
                                'ask_size': ask_size
                            }
                    else:
                        logger.debug(f"市场 {market_id} 订单簿为空")

        except Exception as e:
            logger.debug(f"获取订单簿失败 (market_id={market_id}, outcome_id={outcome_id}): {e}")

        return {'yes_bid': None, 'yes_ask': None, 'bid_size': 0, 'ask_size': 0}

    def get_market_data(self, market_id: Optional[str] = None) -> Optional[MarketData]:
        """
        获取市场数据（使用订单簿价格）

        优先使用 orderbook 价格（真实可成交价格）
        """
        try:
            if not market_id:
                markets = self.get_markets(status='OPEN', limit=10)
                if not markets:
                    return self._default_data('default')
                market = markets[0]
                market_id = market.get('id', 'default')
            else:
                markets = self.get_markets(status='OPEN', limit=100)
                market = next((m for m in markets if str(m.get('id')) == str(market_id)), None)
                if not market:
                    return self._default_data(market_id)

            question = market.get('question') or market.get('title', 'Unknown')
            liquidity = float(market.get('liquidity', 0) or 0)
            volume = float(market.get('volume', 0) or market.get('volume24h', 0) or 0)
            status = market.get('status', 'UNKNOWN')

            # 获取 Yes token 订单簿
            yes_ob = self._get_orderbook(market_id, outcome_id=1)
            yes_bid = yes_ob['yes_bid']
            yes_ask = yes_ob['yes_ask']
            bid_size = yes_ob['bid_size']
            ask_size = yes_ob['ask_size']

            # 对于 No token，Predict.fun 的订单簿 API 可能不支持 outcomeId 参数
            # 使用 1 - yes_bid/yes_ask 估算（这是预测市场的标准做法）
            no_bid = round(1.0 - yes_ask, 4) if yes_ask is not None else None
            no_ask = round(1.0 - yes_bid, 4) if yes_bid is not None else None

            # 如果 Yes 价格获取失败，返回默认数据
            if yes_bid is None or yes_ask is None:
                logger.debug(f"市场 {market_id} 无法获取有效价格")
                return self._default_data(market_id)

            return MarketData(
                market_id=str(market_id),
                question=question,
                current_price=(yes_bid + yes_ask) / 2,
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                no_bid=no_bid,
                no_ask=no_ask,
                best_bid_size=bid_size,
                best_ask_size=ask_size,
                liquidity=liquidity,
                volume=volume,
                timestamp=time.time(),
                status=status
            )

        except Exception as e:
            logger.error(f"获取市场数据失败: {e}")
            return self._default_data(market_id or 'default')

    def get_full_orderbook(self, market_id: str) -> Optional[Dict]:
        """
        获取完整订单簿（Yes 和 No token）

        注意: Predict.fun API 可能不支持 outcomeId 参数来获取 No token 订单簿
        因此 No 价格使用 1 - yes_price 计算

        Returns:
            {'yes_bid': float, 'yes_ask': float, 'no_bid': float, 'no_ask': float}
            或 None（如果 Yes token 订单簿获取失败）
        """
        yes_ob = self._get_orderbook(market_id, outcome_id=1)

        # 检查 Yes token 订单簿是否有效
        if None in [yes_ob['yes_bid'], yes_ob['yes_ask']]:
            logger.debug(f"市场 {market_id} Yes token 订单簿不完整")
            return None

        # Predict.fun API 不支持 outcomeId 参数获取 No token 订单簿
        # 使用标准预测市场公式: No价格 = 1 - Yes价格
        yes_bid = yes_ob['yes_bid']
        yes_ask = yes_ob['yes_ask']

        return {
            'yes_bid': yes_bid,
            'yes_ask': yes_ask,
            'no_bid': round(1.0 - yes_ask, 4),  # No bid = 1 - Yes ask
            'no_ask': round(1.0 - yes_bid, 4)   # No ask = 1 - Yes bid
        }

    def _default_data(self, market_id: str) -> MarketData:
        """默认市场数据（当API失败时使用）"""
        return MarketData(
            market_id=str(market_id),
            question='Default',
            current_price=0.5,
            yes_bid=0.49,
            yes_ask=0.51,
            no_bid=0.49,
            no_ask=0.51,
            best_bid_size=100,
            best_ask_size=100,
            liquidity=0,
            volume=0,
            timestamp=time.time(),
            status='UNKNOWN'
        )

    # ==================== 交易方法（暂不实现，用户专注于监控）====================

    def get_open_orders(self, market_id: Optional[str] = None) -> List[Order]:
        """获取挂单（待实现）"""
        logger.warning("get_open_orders: 交易功能暂未实现")
        return []

    def place_order(self, side: str, price: float, size: float, market_id: Optional[str] = None) -> Optional[Order]:
        """下单（待实现）"""
        logger.warning("place_order: 交易功能暂未实现")
        return None

    def cancel_order(self, order_id: str) -> bool:
        """撤单（待实现）"""
        logger.warning("cancel_order: 交易功能暂未实现")
        return False

    def cancel_all_orders(self, market_id: Optional[str] = None) -> int:
        """撤销所有挂单（待实现）"""
        logger.warning("cancel_all_orders: 交易功能暂未实现")
        return 0

    def clear_cache(self):
        """清除缓存"""
        self._cache.clear()
        self._cache_time = 0
        logger.info("Predict.fun 缓存已清除")


class MockAPIClient:
    """模拟客户端（用于测试）"""

    def __init__(self, config: Dict):
        self.config = config
        self.market_id = config.get('market', {}).get('market_id', 'test-market')
        self.base_price = 0.50
        self._orders = {}
        self._counter = 0

    def get_markets(self, status: str = 'OPEN', sort: str = 'popular', limit: int = 100) -> List[Dict]:
        return self._mock_markets()[:limit]

    def _mock_markets(self) -> List[Dict]:
        import random
        return [
            {
                'id': i + 7000,  # 使用数字ID
                'question': t['question'],
                'title': t['question'][:50],
                'status': 'OPEN',
                'liquidity': random.randint(10000, 100000),
                'volume': random.randint(50000, 500000)
            }
            for i, t in enumerate([
                {'question': 'Will Trump win 2024 election?', 'price': 0.55},
                {'question': 'Bitcoin $100k in 2026?', 'price': 0.65},
                {'question': 'Fed rate below 3%?', 'price': 0.55}
            ])
        ]

    def get_market_data(self) -> MarketData:
        return MarketData(
            market_id=self.market_id,
            question='Mock',
            current_price=0.5,
            yes_bid=0.49,
            yes_ask=0.51,
            no_bid=0.49,
            no_ask=0.51,
            best_bid_size=100,
            best_ask_size=100,
            liquidity=0,
            volume=0,
            timestamp=time.time(),
            status='OPEN'
        )

    def get_open_orders(self) -> List[Order]:
        return list(self._orders.values())

    def place_order(self, side: str, price: float, size: float) -> Order:
        self._counter += 1
        order = Order(f"order_{self._counter}", side, price, size, 'open', time.time())
        self._orders[order.order_id] = order
        return order

    def cancel_order(self, order_id: str) -> bool:
        return order_id in self._orders and bool(self._orders.pop(order_id, None))

    def cancel_all_orders(self) -> int:
        count = len(self._orders)
        self._orders.clear()
        return count

    def clear_cache(self):
        pass


def create_api_client(config: Dict, use_mock: bool = False):
    """
    创建 API 客户端

    Args:
        config: 配置字典
        use_mock: 是否使用模拟客户端
    """
    if use_mock:
        logger.info("使用 Predict.fun 模拟客户端")
        return MockAPIClient(config)
    else:
        logger.info("使用 Predict.fun 真实 API 客户端（v3.0 修复版）")
        return PredictAPIClient(config)
