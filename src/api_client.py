"""
API 客户端模块
负责与 predict.fun 平台通信
支持真实 API 和模拟模式
"""

import time
import random
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class OrderBookEntry:
    """订单簿条目"""
    price: float
    size: float
    orders_count: int


@dataclass
class MarketData:
    """市场数据"""
    market_id: str
    current_price: float
    yes_bid: float      # 买一价
    yes_ask: float      # 卖一价
    best_bid_size: float
    best_ask_size: float
    timestamp: float


@dataclass
class Order:
    """订单信息"""
    order_id: str
    side: str           # 'buy' 或 'sell'
    price: float
    size: float
    status: str         # 'open', 'filled', 'canceled'
    timestamp: float


class PredictAPIClient:
    """
    真实的 Predict.fun API 客户端
    API 文档: https://api.predict.fun/docs
    开发文档: https://dev.predict.fun/
    """

    def __init__(self, config: Dict):
        self.config = config
        self.api_key = config.get('api', {}).get('api_key', '')
        self.base_url = config.get('api', {}).get('base_url', 'https://api.predict.fun')
        self.api_version = 'v1'  # Predict.fun API v1

        # 设置会话
        import requests
        self.session = requests.Session()

        # 设置认证头
        # Predict.fun 使用 x-api-key header，不是 Bearer token
        if self.api_key:
            self.session.headers.update({
                'x-api-key': self.api_key,
                'Content-Type': 'application/json'
            })
        else:
            logger.warning("未设置 PREDICT_API_KEY，某些功能可能无法使用")

        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

        logger.info(f"Predict.fun API 客户端初始化: {self.base_url}")

    def get_markets(self, active_only: bool = True) -> List[Dict]:
        """
        获取市场列表

        Args:
            active_only: 是否只返回活跃市场
        """
        try:
            response = self.session.get(
                f"{self.base_url}/{self.api_version}/markets",
                params={'active': active_only} if active_only else {},
                timeout=15
            )
            response.raise_for_status()
            data = response.json()

            markets = data.get('items', data) if isinstance(data, dict) else data
            logger.info(f"获取到 {len(markets)} 个市场")
            return markets

        except Exception as e:
            logger.error(f"获取市场列表失败: {e}")
            return []

    def get_market_data(self, market_id: Optional[str] = None) -> MarketData:
        """
        获取市场数据（用于套利监控）

        Args:
            market_id: 市场ID（可选，默认使用配置中的市场）
        """
        try:
            if not market_id:
                market_id = self.config.get('market', {}).get('market_id', 'test-market')

            # 获取市场列表
            markets = self.get_markets(active_only=True)

            if not markets:
                # 返回默认值
                return self._get_default_market_data(market_id)

            # 查找指定市场
            target_market = None
            for market in markets:
                if market.get('id') == market_id or market.get('slug') == market_id:
                    target_market = market
                    break

            # 如果没找到指定市场，使用第一个活跃市场
            if not target_market and markets:
                target_market = markets[0]
                market_id = target_market.get('id', market_id)

            if target_market:
                # 解析市场数据
                current_price = self._parse_price(target_market.get('price', 0.5))

                # 获取订单簿数据
                orderbook = self.get_order_book(market_id)
                yes_bid = orderbook.get('yes_bid', current_price * 0.98)
                yes_ask = orderbook.get('yes_ask', current_price * 1.02)

                return MarketData(
                    market_id=market_id,
                    current_price=current_price,
                    yes_bid=yes_bid,
                    yes_ask=yes_ask,
                    best_bid_size=orderbook.get('bid_size', 100),
                    best_ask_size=orderbook.get('ask_size', 100),
                    timestamp=time.time()
                )

            return self._get_default_market_data(market_id)

        except Exception as e:
            logger.error(f"获取市场数据失败: {e}")
            return self._get_default_market_data(market_id or 'test-market')

    def _parse_price(self, price) -> float:
        """解析价格"""
        if isinstance(price, (int, float)):
            return float(max(0.01, min(0.99, price)))
        if isinstance(price, str):
            try:
                return float(max(0.01, min(0.99, float(price))))
            except:
                pass
        return 0.5

    def _get_default_market_data(self, market_id: str) -> MarketData:
        """返回默认市场数据"""
        return MarketData(
            market_id=market_id,
            current_price=0.5,
            yes_bid=0.49,
            yes_ask=0.51,
            best_bid_size=100,
            best_ask_size=100,
            timestamp=time.time()
        )

    def get_order_book(self, market_id: str) -> Dict:
        """
        获取订单簿

        Args:
            market_id: 市场ID
        """
        try:
            response = self.session.get(
                f"{self.base_url}/{self.api_version}/markets/{market_id}/orderbook",
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()

                # 解析订单簿数据
                bids = data.get('bids', [])
                asks = data.get('asks', [])

                yes_bid = float(bids[0]['price']) if bids else 0.49
                yes_ask = float(asks[0]['price']) if asks else 0.51

                return {
                    'yes_bid': yes_bid,
                    'yes_ask': yes_ask,
                    'bid_size': float(bids[0]['amount']) if bids else 100,
                    'ask_size': float(asks[0]['amount']) if asks else 100
                }

        except Exception as e:
            logger.debug(f"获取订单簿失败 {market_id}: {e}")

        # 返回默认值
        return {
            'yes_bid': 0.49,
            'yes_ask': 0.51,
            'bid_size': 100,
            'ask_size': 100
        }

    def get_open_orders(self, market_id: Optional[str] = None) -> List[Order]:
        """
        获取当前所有挂单

        Args:
            market_id: 市场ID（可选）
        """
        try:
            if not self.api_key:
                logger.warning("需要 API Key 才能获取订单")
                return []

            params = {'market_id': market_id} if market_id else {}
            response = self.session.get(
                f"{self.base_url}/{self.api_version}/orders",
                params=params,
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            orders = []
            for order_data in data.get('items', data) if isinstance(data, dict) else data:
                orders.append(Order(
                    order_id=str(order_data.get('id', '')),
                    side=order_data.get('side', 'buy'),
                    price=float(order_data.get('price', 0)),
                    size=float(order_data.get('amount', 0)),
                    status=order_data.get('status', 'open'),
                    timestamp=time.time()
                ))

            return orders

        except Exception as e:
            logger.error(f"获取订单失败: {e}")
            return []

    def place_order(self, side: str, price: float, size: float,
                    market_id: Optional[str] = None) -> Optional[Order]:
        """
        下单

        Args:
            side: 'buy' 或 'sell'
            price: 价格
            size: 数量
            market_id: 市场ID（可选）
        """
        try:
            if not self.api_key:
                logger.warning("需要 API Key 才能下单")
                return None

            if not market_id:
                market_id = self.config.get('market', {}).get('market_id', 'test-market')

            payload = {
                'market_id': market_id,
                'side': side.lower(),
                'price': price,
                'amount': size,
                'type': 'limit'  # 限价单
            }

            response = self.session.post(
                f"{self.base_url}/{self.api_version}/orders",
                json=payload,
                timeout=15
            )
            response.raise_for_status()
            data = response.json()

            logger.info(f"下单成功: {side} {size} @ {price}")

            return Order(
                order_id=str(data.get('id', '')),
                side=side,
                price=price,
                size=size,
                status='open',
                timestamp=time.time()
            )

        except Exception as e:
            logger.error(f"下单失败: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """
        撤单

        Args:
            order_id: 订单ID
        """
        try:
            if not self.api_key:
                logger.warning("需要 API Key 才能撤单")
                return False

            response = self.session.delete(
                f"{self.base_url}/{self.api_version}/orders/{order_id}",
                timeout=10
            )
            response.raise_for_status()

            logger.info(f"撤单成功: {order_id}")
            return True

        except Exception as e:
            logger.error(f"撤单失败: {e}")
            return False

    def cancel_all_orders(self, market_id: Optional[str] = None) -> int:
        """
        撤销所有挂单

        Args:
            market_id: 市场ID（可选）
        """
        try:
            if not self.api_key:
                logger.warning("需要 API Key 才能撤单")
                return 0

            orders = self.get_open_orders(market_id)
            canceled = 0

            for order in orders:
                if self.cancel_order(order.order_id):
                    canceled += 1

            logger.info(f"撤销了 {canceled} 个订单")
            return canceled

        except Exception as e:
            logger.error(f"批量撤单失败: {e}")
            return 0


class MockAPIClient:
    """
    模拟 API 客户端
    用于测试策略逻辑，等待真实 API 批准后替换
    """

    def __init__(self, config: Dict):
        self.config = config
        self.market_id = config.get('market', {}).get('market_id', 'test-market')
        self.base_price = 0.50  # 模拟基础价格

        # 模拟订单存储
        self._orders: Dict[str, Order] = {}
        self._order_counter = 0

        # 模拟价格波动
        self._price_history = [self.base_price]

    def get_market_data(self) -> MarketData:
        """获取当前市场数据（模拟）"""
        # 模拟价格随机波动 ±2%
        change = random.uniform(-0.02, 0.02)
        self.base_price = max(0.01, min(0.99, self.base_price * (1 + change)))
        self._price_history.append(self.base_price)

        # 模拟买卖价差
        spread = random.uniform(0.01, 0.03)
        yes_bid = round(self.base_price - spread / 2, 3)
        yes_ask = round(self.base_price + spread / 2, 3)

        return MarketData(
            market_id=self.market_id,
            current_price=round(self.base_price, 3),
            yes_bid=max(0.01, yes_bid),
            yes_ask=min(0.99, yes_ask),
            best_bid_size=random.uniform(100, 1000),
            best_ask_size=random.uniform(100, 1000),
            timestamp=time.time()
        )

    def get_markets(self, active_only: bool = True) -> List[Dict]:
        """获取市场列表（模拟）"""
        return [{
            'id': 'test-market-1',
            'slug': 'test-market',
            'question': '测试市场：某事件将在2026年发生',
            'price': self.base_price,
            'active': True
        }]

    def get_open_orders(self) -> List[Order]:
        """获取当前所有挂单"""
        return list(self._orders.values())

    def place_order(self, side: str, price: float, size: float) -> Order:
        """下单"""
        self._order_counter += 1
        order = Order(
            order_id=f"order_{self._order_counter}",
            side=side,
            price=price,
            size=size,
            status='open',
            timestamp=time.time()
        )
        self._orders[order.order_id] = order
        return order

    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        if order_id in self._orders:
            self._orders[order_id].status = 'canceled'
            del self._orders[order_id]
            return True
        return False

    def cancel_all_orders(self) -> int:
        """撤销所有挂单"""
        count = len(self._orders)
        self._orders.clear()
        return count

    def get_order_book(self, market_id: str) -> Dict:
        """获取订单簿（模拟）"""
        spread = random.uniform(0.01, 0.03)
        return {
            'yes_bid': round(self.base_price - spread / 2, 3),
            'yes_ask': round(self.base_price + spread / 2, 3),
            'bid_size': random.uniform(100, 1000),
            'ask_size': random.uniform(100, 1000)
        }


def create_api_client(config: Dict, use_mock: bool = True):
    """
    创建 API 客户端工厂函数

    Args:
        config: 配置字典
        use_mock: 是否使用模拟客户端（默认True）

    Returns:
        API 客户端实例
    """
    if use_mock:
        logger.info("使用 Predict.fun 模拟客户端")
        return MockAPIClient(config)
    else:
        logger.info("使用 Predict.fun 真实 API 客户端")
        return PredictAPIClient(config)
