"""
API 客户端模块
负责与 predict.fun 平台通信
注意：当前版本使用模拟数据，待 API 批准后接入真实接口
"""

import time
import random
from typing import Dict, List, Optional
from dataclasses import dataclass


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


class APIClient:
    """
    真实 API 客户端（待实现）
    当获得 API 访问权限后，基于官方文档实现此类
    """

    def __init__(self, config: Dict):
        self.config = config
        self.api_key = config.get('api', {}).get('api_key', '')
        self.base_url = config.get('api', {}).get('base_url', '')
        raise NotImplementedError(
            "真实 API 客户端待实现。\n"
            "请在获得 API 访问权限后，基于官方文档实现。\n"
            "当前使用 MockAPIClient 进行测试。"
        )

    def get_market_data(self) -> MarketData:
        raise NotImplementedError

    def get_open_orders(self) -> List[Order]:
        raise NotImplementedError

    def place_order(self, side: str, price: float, size: float) -> Order:
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError


def create_api_client(config: Dict, use_mock: bool = True) -> MockAPIClient:
    """
    创建 API 客户端工厂函数

    Args:
        config: 配置字典
        use_mock: 是否使用模拟客户端（默认True）

    Returns:
        API 客户端实例
    """
    if use_mock:
        return MockAPIClient(config)
    else:
        return APIClient(config)
