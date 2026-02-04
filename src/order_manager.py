"""
订单管理模块
协调策略和风险管理，执行订单操作
"""

import time
import logging
from typing import List, Optional

from .api_client import MockAPIClient, Order, MarketData
from .strategy import Strategy, PositionManager, OrderLevel
from .risk_manager import RiskManager, RiskCheckResult


class OrderManager:
    """
    订单管理器
    协调策略执行和风险控制
    """

    def __init__(self, api_client: MockAPIClient, strategy: Strategy,
                 position_manager: PositionManager, risk_manager: RiskManager,
                 config: dict):
        self.api_client = api_client
        self.strategy = strategy
        self.position_manager = position_manager
        self.risk_manager = risk_manager
        self.config = config

        # 配置日志
        self.logger = logging.getLogger(__name__)
        self.refresh_interval = config.get('strategy', {}).get('refresh_interval', 5)

        # 运行状态
        self.running = False
        self.iteration_count = 0

    def start(self):
        """启动订单管理循环"""
        self.running = True
        self.logger.info("订单管理器启动")

        try:
            while self.running:
                self._run_iteration()
                self.iteration_count += 1
                time.sleep(self.refresh_interval)

        except KeyboardInterrupt:
            self.logger.info("收到停止信号，正在清理...")
        except Exception as e:
            self.logger.error(f"运行时错误: {e}", exc_info=True)
        finally:
            self.stop()

    def stop(self):
        """停止订单管理器"""
        self.running = False
        self.logger.info("订单管理器停止")

        # 撤销所有挂单
        try:
            canceled = self.api_client.cancel_all_orders()
            self.logger.info(f"已撤销 {canceled} 个挂单")
        except Exception as e:
            self.logger.error(f"撤单失败: {e}")

    def _run_iteration(self):
        """执行一次迭代"""
        # 1. 获取市场数据
        market_data = self.api_client.get_market_data()
        self.logger.debug(
            f"市场价格: {market_data.current_price:.3f}, "
            f"买一: {market_data.yes_bid:.3f}, "
            f"卖一: {market_data.yes_ask:.3f}"
        )

        # 2. 获取当前挂单
        open_orders = self.api_client.get_open_orders()

        # 3. 风险检查
        risk_result = self.risk_manager.check_risk(market_data, self.position_manager, open_orders)

        if not risk_result.passed:
            self.logger.warning(f"风险检查失败: {risk_result.message}")
            if risk_result.level.value == "critical":
                self.logger.critical("达到风险临界值，暂停交易")
                return

        # 4. 检查需要撤单的订单
        orders_to_cancel = self.risk_manager.check_orders_for_cancellation(
            open_orders, market_data
        )

        for order_id in orders_to_cancel:
            self.api_client.cancel_order(order_id)
            self.logger.info(f"撤单: {order_id} (价格接近)")

        # 5. 计算并挂出新订单
        buy_levels, sell_levels = self.strategy.calculate_order_levels(
            market_data, open_orders
        )

        # 挂买入订单
        for level in buy_levels:
            order = self.api_client.place_order(level.side, level.price, level.size)
            self.logger.info(
                f"挂买单: 价格={level.price:.3f}, 数量={level.size}, "
                f"订单ID={order.order_id}"
            )

        # 挂卖出订单
        for level in sell_levels:
            order = self.api_client.place_order(level.side, level.price, level.size)
            self.logger.info(
                f"挂卖单: 价格={level.price:.3f}, 数量={level.size}, "
                f"订单ID={order.order_id}"
            )

        # 6. 定期输出状态
        if self.iteration_count % 10 == 0:
            self._print_status(market_data, risk_result)

    def _print_status(self, market_data: MarketData, risk_result: RiskCheckResult):
        """输出当前状态摘要"""
        open_orders = self.api_client.get_open_orders()
        positions = self.position_manager.get_positions()

        self.logger.info("=" * 50)
        self.logger.info(f"迭代次数: {self.iteration_count}")
        self.logger.info(f"市场价格: {market_data.current_price:.3f}")
        self.logger.info(f"活跃订单: {len(open_orders)} 个")
        self.logger.info(f"持仓: Yes={positions['yes_position']:.2f}, No={positions['no_position']:.2f}")
        self.logger.info(f"风险敞口: {risk_result.exposure:.2f}/{risk_result.max_exposure:.2f}")
        self.logger.info(f"风险等级: {risk_result.level.value.upper()}")
        self.logger.info("=" * 50)
