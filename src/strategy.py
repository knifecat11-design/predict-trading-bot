"""
挂单策略模块
实现 ±6% 范围内智能挂单策略
"""

import math
from typing import List, Tuple
from dataclasses import dataclass

from .api_client import MarketData, Order


@dataclass
class OrderLevel:
    """挂单层级"""
    price: float
    size: float
    side: str  # 'buy' or 'sell'


class Strategy:
    """
    挂单策略类
    在当前价格 ±6% 范围内挂单，获取挂单奖励积分
    """

    def __init__(self, config: dict):
        self.spread_percent = config.get('strategy', {}).get('spread_percent', 6.0)
        self.base_position_size = config.get('market', {}).get('base_position_size', 10)
        self.max_orders_per_side = config.get('risk', {}).get('max_orders_per_side', 3)
        self.min_price = config.get('risk', {}).get('min_price', 0.01)
        self.max_price = config.get('risk', {}).get('max_price', 0.99)

    def calculate_order_levels(self, market_data: MarketData,
                               existing_orders: List[Order]) -> Tuple[List[OrderLevel], List[OrderLevel]]:
        """
        计算最优挂单位置

        Args:
            market_data: 市场数据
            existing_orders: 现有挂单列表

        Returns:
            (买入订单列表, 卖出订单列表)
        """
        current_price = market_data.current_price

        # 计算挂单价格范围
        buy_price = current_price * (1 - self.spread_percent / 100)
        sell_price = current_price * (1 + self.spread_percent / 100)

        # 价格限制检查
        buy_price = max(self.min_price, buy_price)
        sell_price = min(self.max_price, sell_price)

        # 检查是否已有订单
        has_buy_order = any(o.side == 'buy' for o in existing_orders)
        has_sell_order = any(o.side == 'sell' for o in existing_orders)

        buy_orders = []
        sell_orders = []

        # 生成买入订单（在当前价格下方）
        if not has_buy_order:
            buy_orders.append(OrderLevel(
                price=round(buy_price, 3),
                size=self.base_position_size,
                side='buy'
            ))

        # 生成卖出订单（在当前价格上方）
        if not has_sell_order:
            sell_orders.append(OrderLevel(
                price=round(sell_price, 3),
                size=self.base_position_size,
                side='sell'
            ))

        return buy_orders, sell_orders

    def should_cancel_order(self, order: Order, market_data: MarketData,
                           threshold_percent: float) -> bool:
        """
        判断是否应该撤单

        当市场价格接近挂单价时撤单，避免意外成交

        Args:
            order: 订单
            market_data: 当前市场数据
            threshold_percent: 撤单阈值百分比

        Returns:
            是否应该撤单
        """
        current_price = market_data.current_price

        if order.side == 'buy':
            # 买单：当市场价接近买单价时撤单
            distance = (current_price - order.price) / order.price
        else:  # sell
            # 卖单：当市场价接近卖单价时撤单
            distance = (order.price - current_price) / order.price

        # 距离小于阈值时撤单
        return distance < threshold_percent / 100

    def calculate_position_value(self, orders: List[Order]) -> float:
        """
        计算当前仓位价值

        Args:
            orders: 订单列表

        Returns:
            总仓位价值
        """
        total = sum(o.price * o.size for o in orders if o.status == 'open')
        return round(total, 2)


class PositionManager:
    """
    仓位管理器
    管理基础仓位和风险敞口
    """

    def __init__(self, config: dict):
        self.base_position_size = config.get('market', {}).get('base_position_size', 10)
        self.max_exposure = config.get('market', {}).get('max_exposure', 100)

        # 模拟持仓记录
        self.yes_position = 0.0  # Yes代币持仓
        self.no_position = 0.0   # No代币持仓（通过卖出Yes获得）

    def get_net_exposure(self, current_price: float) -> float:
        """
        计算净风险敞口

        Args:
            current_price: 当前市场价格

        Returns:
            净敞口金额
        """
        yes_value = self.yes_position * current_price
        no_value = self.no_position * (1 - current_price)
        return yes_value - no_value

    def is_within_limits(self, current_price: float) -> bool:
        """
        检查风险敞口是否在限制内

        Args:
            current_price: 当前市场价格

        Returns:
            是否在限制内
        """
        exposure = abs(self.get_net_exposure(current_price))
        return exposure <= self.max_exposure

    def update_position(self, side: str, size: float, price: float):
        """
        更新持仓（成交后调用）

        Args:
            side: 'buy' 或 'sell'
            size: 成交数量
            price: 成交价格
        """
        if side == 'buy':
            # 买入Yes，获得Yes代币
            self.yes_position += size
        else:
            # 卖出Yes，获得No代币（模拟）
            self.no_position += size

    def get_positions(self) -> dict:
        """获取当前持仓信息"""
        return {
            'yes_position': self.yes_position,
            'no_position': self.no_position
        }
