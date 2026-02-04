"""
风险管理模块
处理风险敞口、止损和仓位控制
"""

import time
from typing import List, Dict, Optional
from dataclasses import dataclass
from enum import Enum

from .api_client import MarketData, Order


class RiskLevel(Enum):
    """风险等级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RiskCheckResult:
    """风险检查结果"""
    passed: bool
    level: RiskLevel
    message: str
    exposure: float
    max_exposure: float


class RiskManager:
    """
    风险管理器
    监控风险敞口，在风险过高时暂停交易
    """

    def __init__(self, config: dict):
        self.max_exposure = config.get('market', {}).get('max_exposure', 100)
        self.daily_loss_limit = config.get('risk', {}).get('daily_loss_limit', 50)
        self.cancel_threshold = config.get('strategy', {}).get('cancel_threshold', 0.5)

        # 每日损失追踪
        self.daily_pnl = 0.0
        self.daily_start_value = 0.0
        self.last_reset_date = time.localtime(time.time()).tm_yday

        # 风险事件日志
        self.risk_events: List[Dict] = []

    def check_risk(self, market_data: MarketData,
                   position_manager, open_orders: List[Order]) -> RiskCheckResult:
        """
        执行风险检查

        Args:
            market_data: 当前市场数据
            position_manager: 仓位管理器
            open_orders: 当前挂单列表

        Returns:
            风险检查结果
        """
        # 检查是否需要重置每日统计
        self._check_daily_reset()

        # 计算当前风险敞口
        exposure = position_manager.get_net_exposure(market_data.current_price)

        # 判断风险等级
        exposure_ratio = abs(exposure) / self.max_exposure if self.max_exposure > 0 else 0

        if exposure_ratio >= 1.0:
            level = RiskLevel.CRITICAL
            passed = False
            message = f"风险敞口超限！当前: {exposure:.2f}, 最大: {self.max_exposure}"
        elif exposure_ratio >= 0.8:
            level = RiskLevel.HIGH
            passed = True
            message = f"风险敞口较高: {exposure:.2f}/{self.max_exposure}"
        elif exposure_ratio >= 0.5:
            level = RiskLevel.MEDIUM
            passed = True
            message = f"风险敞口中等: {exposure:.2f}/{self.max_exposure}"
        else:
            level = RiskLevel.LOW
            passed = True
            message = f"风险敞口正常: {exposure:.2f}/{self.max_exposure}"

        # 检查每日损失
        if self.daily_pnl < -self.daily_loss_limit:
            passed = False
            level = RiskLevel.CRITICAL
            message = f"每日损失超限！当前: {self.daily_pnl:.2f}, 限制: {self.daily_loss_limit}"

        return RiskCheckResult(
            passed=passed,
            level=level,
            message=message,
            exposure=exposure,
            max_exposure=self.max_exposure
        )

    def check_orders_for_cancellation(self, orders: List[Order],
                                     market_data: MarketData) -> List[str]:
        """
        检查哪些订单需要撤单（价格接近时）

        Args:
            orders: 挂单列表
            market_data: 市场数据

        Returns:
            需要撤单的订单ID列表
        """
        to_cancel = []

        for order in orders:
            if order.status != 'open':
                continue

            current_price = market_data.current_price

            if order.side == 'buy':
                # 买单：当市价接近买单价时撤单
                distance = (current_price - order.price) / order.price * 100
            else:  # sell
                # 卖单：当市价接近卖单价时撤单
                distance = (order.price - current_price) / order.price * 100

            # 距离小于阈值时标记撤单
            if distance < self.cancel_threshold:
                to_cancel.append(order.order_id)

                # 记录风险事件
                self._log_risk_event({
                    'type': 'order_cancel',
                    'order_id': order.order_id,
                    'reason': f'价格接近 (距离: {distance:.3f}%)',
                    'order_price': order.price,
                    'market_price': current_price,
                    'timestamp': time.time()
                })

        return to_cancel

    def record_pnl(self, pnl: float):
        """
        记录盈亏

        Args:
            pnl: 盈亏金额（正数为盈利，负数为亏损）
        """
        self.daily_pnl += pnl

    def get_daily_summary(self) -> Dict:
        """获取每日汇总"""
        return {
            'daily_pnl': self.daily_pnl,
            'daily_loss_limit': self.daily_loss_limit,
            'remaining_loss_limit': self.daily_loss_limit + self.daily_pnl,
            'risk_events_count': len(self.risk_events)
        }

    def get_recent_risk_events(self, limit: int = 10) -> List[Dict]:
        """
        获取最近的风险事件

        Args:
            limit: 返回数量限制

        Returns:
            风险事件列表
        """
        return self.risk_events[-limit:]

    def _check_daily_reset(self):
        """检查并重置每日统计"""
        current_day = time.localtime(time.time()).tm_yday
        if current_day != self.last_reset_date:
            self.last_reset_date = current_day
            self.daily_pnl = 0.0
            self.risk_events.clear()

    def _log_risk_event(self, event: Dict):
        """记录风险事件"""
        event['logged_at'] = time.time()
        self.risk_events.append(event)
