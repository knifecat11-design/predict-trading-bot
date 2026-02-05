"""
套利监控模块
监控 Polymarket 和 Predict.fun 之间的套利机会
策略：Yes价格 + No价格 < 100% 时存在套利空间
支持 WebSocket 实时监控
"""

import time
import logging
import asyncio
from typing import Dict, List, Optional, Tuple, Callable
from dataclasses import dataclass
from enum import Enum
from collections import defaultdict
from datetime import datetime


class ArbitrageType(Enum):
    """套利类型"""
    POLY_YES_PREDICT_NO = "poly_yes_predict_no"      # Poly买Yes + Predict买No
    PREDICT_YES_POLY_NO = "predict_yes_poly_no"      # Predict买Yes + Poly买No


@dataclass
class ArbitrageOpportunity:
    """套利机会"""
    arbitrage_type: ArbitrageType
    market_name: str

    # 利差信息
    combined_price: float          # Yes + No 的总价格
    arbitrage_percent: float       # 套利空间（100% - combined_price）

    # 平台1信息 (Polymarket 或 Probable)
    platform1_name: str            # 平台名称
    platform1_yes_price: float     # 平台1 Yes价格
    platform1_no_price: float      # 平台1 No价格
    platform1_action: str          # "买Yes" 或 "买No"

    # 平台2信息 (Predict 或 Probable)
    platform2_name: str            # 平台名称
    platform2_yes_price: float     # 平台2 Yes价格
    platform2_no_price: float      # 平台2 No价格
    platform2_action: str          # "买Yes" 或 "买No"

    timestamp: float


@dataclass
class ArbitrageConfig:
    """套利配置"""
    min_arbitrage_percent: float   # 最小套利空间阈值（%）
    scan_interval: int             # 扫描间隔（秒）


class ArbitrageMonitor:
    """
    套利监控器
    监控 Yes + No 的价格差，发现套利机会
    """

    def __init__(self, config: Dict):
        self.config = config
        arb_config = config.get('arbitrage', {})

        self.arb_config = ArbitrageConfig(
            min_arbitrage_percent=arb_config.get('min_arbitrage_threshold', 2.0),  # 默认2%
            scan_interval=arb_config.get('scan_interval', 10)
        )

        self.logger = logging.getLogger(__name__)

        # 统计信息
        self.total_scans = 0
        self.opportunities_found = 0
        self.realtime_updates = 0

        # 智能市场匹配器
        self.market_matcher = None
        self.market_map = {}
        self._matcher_initialized = False

        # WebSocket 实时监控
        self._ws_client = None
        self._ws_running = False
        self._realtime_opportunities = []
        self._on_arbitrage_callback: Optional[Callable] = None

    def _initialize_matcher(self, poly_client, predict_client, probable_client):
        """初始化市场匹配器"""
        try:
            from .market_matcher import create_market_matcher
            self.market_matcher = create_market_matcher(self.config)
            self._matcher_initialized = True
            self.logger.info("智能市场匹配器初始化成功")
        except ImportError as e:
            self.logger.warning(f"无法导入市场匹配器: {e}")
            self._matcher_initialized = False

    def check_arbitrage(self,
                       platform1_yes_price: float,
                       platform2_no_price: float,
                       market_name: str,
                       platform1_name: str = "Polymarket",
                       platform2_name: str = "Predict.fun") -> Optional[ArbitrageOpportunity]:
        """
        检查套利机会：平台1 Yes + 平台2 No

        Args:
            platform1_yes_price: 平台1 Yes价格
            platform2_no_price: 平台2 No价格
            market_name: 市场名称
            platform1_name: 平台1名称
            platform2_name: 平台2名称

        Returns:
            套利机会（如果存在）
        """
        # No价格转换为小数形式（如果输入是百分比如50，需要除以100）
        if platform2_no_price > 1:
            platform2_no_price = platform2_no_price / 100

        # 计算组合价格
        combined = platform1_yes_price + platform2_no_price

        # 计算套利空间
        arbitrage = (1.0 - combined) * 100  # 转换为百分比

        # 检查是否满足阈值
        if arbitrage >= self.arb_config.min_arbitrage_percent:
            # 确定套利类型
            if platform1_name == "Polymarket" and platform2_name == "Predict.fun":
                arb_type = ArbitrageType.POLY_YES_PREDICT_NO
            else:
                arb_type = ArbitrageType.POLY_YES_PREDICT_NO

            return ArbitrageOpportunity(
                arbitrage_type=arb_type,
                market_name=market_name,
                combined_price=combined * 100,  # 转换为百分比显示
                arbitrage_percent=round(arbitrage, 2),
                platform1_name=platform1_name,
                platform1_yes_price=platform1_yes_price * 100,  # 转换为百分比显示
                platform1_no_price=round((1 - platform1_yes_price) * 100, 1),
                platform1_action="买Yes",
                platform2_name=platform2_name,
                platform2_yes_price=round((1 - platform2_no_price) * 100, 1),
                platform2_no_price=platform2_no_price * 100,
                platform2_action="买No",
                timestamp=time.time()
            )

        return None

    def check_reverse_arbitrage(self,
                               platform1_yes_price: float,
                               platform2_no_price: float,
                               market_name: str,
                               platform1_name: str = "Predict.fun",
                               platform2_name: str = "Polymarket") -> Optional[ArbitrageOpportunity]:
        """
        检查反向套利机会：平台1 Yes + 平台2 No

        Args:
            platform1_yes_price: 平台1 Yes价格
            platform2_no_price: 平台2 No价格
            market_name: 市场名称
            platform1_name: 平台1名称
            platform2_name: 平台2名称

        Returns:
            套利机会（如果存在）
        """
        # No价格转换
        if platform2_no_price > 1:
            platform2_no_price = platform2_no_price / 100

        # 计算组合价格
        combined = platform1_yes_price + platform2_no_price

        # 计算套利空间
        arbitrage = (1.0 - combined) * 100

        # 检查是否满足阈值
        if arbitrage >= self.arb_config.min_arbitrage_percent:
            # 确定套利类型
            if platform1_name == "Predict.fun" and platform2_name == "Polymarket":
                arb_type = ArbitrageType.PREDICT_YES_POLY_NO
            else:
                arb_type = ArbitrageType.PREDICT_YES_POLY_NO

            return ArbitrageOpportunity(
                arbitrage_type=arb_type,
                market_name=market_name,
                combined_price=combined * 100,
                arbitrage_percent=round(arbitrage, 2),
                platform1_name=platform1_name,
                platform1_yes_price=platform1_yes_price * 100,
                platform1_no_price=round((1 - platform1_yes_price) * 100, 1),
                platform1_action="买Yes",
                platform2_name=platform2_name,
                platform2_yes_price=round((1 - platform2_no_price) * 100, 1),
                platform2_no_price=platform2_no_price * 100,
                platform2_action="买No",
                timestamp=time.time()
            )

        return None

    def scan_all_markets(self,
                        poly_client,
                        predict_client,
                        probable_client=None) -> List[ArbitrageOpportunity]:
        """
        扫描所有市场寻找套利机会

        Args:
            poly_client: Polymarket客户端
            predict_client: Predict客户端
            probable_client: Probable客户端（可选）

        Returns:
            套利机会列表
        """
        self.total_scans += 1
        opportunities = []

        # 初始化或更新市场匹配器
        if not self._matcher_initialized:
            self._initialize_matcher(poly_client, predict_client, probable_client)

        # 使用智能匹配的市场映射
        market_matches = self.market_matcher.build_market_map(
            poly_client, predict_client, probable_client
        )

        self.logger.debug(f"扫描 {len(market_matches)} 个匹配的市场")

        for poly_market_id, match in market_matches.items():
            try:
                # 获取 Polymarket 市场数据
                poly_market = poly_client.get_market_info(poly_market_id)
                if not poly_market:
                    self.logger.warning(f"无法获取市场信息: {poly_market_id}")
                    continue

                poly_orderbook = poly_client.get_order_book(poly_market_id)
                if not poly_orderbook:
                    self.logger.warning(f"无法获取订单簿: {poly_market_id}")
                    continue

                # 检查必要的属性是否存在
                if not all(hasattr(poly_market, attr) for attr in ['current_price', 'question_title']):
                    self.logger.error(f"poly_market 缺少必要属性")
                    continue

                if not hasattr(poly_orderbook, 'yes_bid'):
                    self.logger.error(f"poly_orderbook 缺少 yes_bid 属性")
                    continue

                # 尝试获取 Predict.fun 匹配市场的数据
                if match.predict_id:
                    try:
                        predict_market = predict_client.get_market_data(match.predict_id)
                    except:
                        predict_market = predict_client.get_market_data()
                else:
                    predict_market = None

                # Probable.markets 已弃用，跳过相关逻辑

                # 检查 Predict.fun 套利机会
                if predict_market and all(hasattr(predict_market, attr) for attr in ['yes_bid', 'current_price']):
                    # 方向1: Polymarket Yes + Predict No
                    opp1 = self.check_arbitrage(
                        poly_market.current_price,
                        predict_market.yes_bid,
                        poly_market.question_title,
                        "Polymarket",
                        "Predict.fun"
                    )

                    if opp1:
                        opportunities.append(opp1)
                        self.opportunities_found += 1
                        self.logger.info(f"发现套利机会: {opp1.market_name} (Poly Yes + Predict No) 置信度: {match.confidence}")

                    # 方向2: Predict Yes + Polymarket No
                    opp2 = self.check_reverse_arbitrage(
                        predict_market.current_price,
                        poly_orderbook.yes_bid,
                        poly_market.question_title,
                        "Predict.fun",
                        "Polymarket"
                    )

                    if opp2:
                        opportunities.append(opp2)
                        self.opportunities_found += 1
                        self.logger.info(f"发现套利机会: {opp2.market_name} (Predict Yes + Poly No) 置信度: {match.confidence}")

            except AttributeError as e:
                self.logger.error(f"扫描市场 {poly_market_id} 时属性错误: {e}")
            except TypeError as e:
                self.logger.error(f"扫描市场 {poly_market_id} 时类型错误: {e}")
            except Exception as e:
                self.logger.error(f"扫描市场 {poly_market_id} 时未知错误: {type(e).__name__}: {e}", exc_info=True)

        return opportunities

    def get_statistics(self) -> Dict:
        """获取统计信息"""
        stats = {
            'total_scans': self.total_scans,
            'opportunities_found': self.opportunities_found,
            'min_arbitrage_threshold': self.arb_config.min_arbitrage_percent
        }

        # 添加市场匹配统计
        if self.market_matcher:
            stats['market_matches'] = self.market_matcher.get_statistics()

        # 添加 WebSocket 统计
        if self._ws_client:
            stats['websocket'] = self._ws_client.get_statistics()
            stats['realtime_updates'] = self.realtime_updates

        return stats

    async def start_realtime_monitoring(self,
                                       poly_client,
                                       predict_client,
                                       probable_client=None,
                                       duration_seconds: int = 3600):
        """
        启动 WebSocket 实时监控

        Args:
            poly_client: Polymarket客户端
            predict_client: Predict客户端
            probable_client: Probable客户端（可选）
            duration_seconds: 监控持续时间（秒）

        Returns:
            发现的套利机会列表
        """
        try:
            from .polymarket_websocket import create_websocket_client
        except ImportError:
            self.logger.error("无法导入 WebSocket 客户端")
            return []

        self.logger.info(f"启动 WebSocket 实时监控 ({duration_seconds} 秒)...")

        # 初始化市场匹配器
        if not self._matcher_initialized:
            self._initialize_matcher(poly_client, predict_client, probable_client)

        # 获取市场列表
        markets = poly_client.get_all_markets(limit=1000, active_only=True)
        self.logger.info(f"获取到 {len(markets)} 个活跃市场用于 WebSocket 监控")

        # 创建 WebSocket 客户端
        ws_config = self.config.get('websocket', {})
        self._ws_client = create_websocket_client(
            num_connections=ws_config.get('num_connections', 6),
            markets_per_connection=ws_config.get('markets_per_connection', 250),
            min_liquidity=ws_config.get('min_liquidity', 10000),
            max_days=ws_config.get('max_days', 7)
        )

        # 注册回调
        async def on_market_update(market_id, prices):
            """实时检测套利机会"""
            self.realtime_updates += 1

            yes_price = prices.get('yes', 0)
            no_price = prices.get('no', 0)

            if yes_price > 0 and no_price > 0:
                spread = yes_price + no_price

                # 检查是否满足套利阈值
                arbitrage = (1.0 - spread) * 100
                if arbitrage >= self.arb_config.min_arbitrage_percent:
                    # 获取市场信息
                    market = poly_client.get_market_info(market_id)
                    if market and hasattr(market, 'question_title'):
                        opportunity = ArbitrageOpportunity(
                            arbitrage_type=ArbitrageType.POLY_YES_PREDICT_NO,
                            market_name=market.question_title,
                            combined_price=spread * 100,
                            arbitrage_percent=round(arbitrage, 2),
                            platform1_name="Polymarket",
                            platform1_yes_price=yes_price * 100,
                            platform1_no_price=round((1 - yes_price) * 100, 1),
                            platform1_action="买Yes",
                            platform2_name="Polymarket",
                            platform2_yes_price=round((1 - no_price) * 100, 1),
                            platform2_no_price=no_price * 100,
                            platform2_action="买No",
                            timestamp=time.time()
                        )

                        self._realtime_opportunities.append(opportunity)
                        self.opportunities_found += 1

                        self.logger.info(
                            f"[实时] 发现套利机会: {market.question_title[:50]}... "
                            f"Spread={spread:.4f} ({arbitrage:.2f}%)"
                        )

                        # 触发回调
                        if self._on_arbitrage_callback:
                            try:
                                if asyncio.iscoroutinefunction(self._on_arbitrage_callback):
                                    await self._on_arbitrage_callback(opportunity)
                                else:
                                    self._on_arbitrage_callback(opportunity)
                            except Exception as e:
                                self.logger.error(f"回调函数错误: {e}")

        self._ws_client.on_market_update(on_market_update)

        # 启动监控
        self._ws_running = True
        monitor_task = asyncio.create_task(self._ws_client.connect(markets))

        # 运行指定时间后停止
        try:
            await asyncio.sleep(duration_seconds)

            self.logger.info("监控时间结束，正在停止...")
            await self._ws_client.disconnect()
            self._ws_running = False

            # 等待任务完成
            await asyncio.sleep(2)

        except KeyboardInterrupt:
            self.logger.info("用户中断监控")
            await self._ws_client.disconnect()
            self._ws_running = False

        self.logger.info(f"实时监控结束，发现 {len(self._realtime_opportunities)} 个套利机会")

        return self._realtime_opportunities

    def on_arbitrage(self, callback: Callable):
        """
        注册套利机会回调函数

        Args:
            callback: 套利机会发生时调用的函数
        """
        self._on_arbitrage_callback = callback

    def get_realtime_opportunities(self) -> List[ArbitrageOpportunity]:
        """获取实时监控发现的套利机会"""
        return self._realtime_opportunities

    def clear_realtime_opportunities(self):
        """清除实时套利机会缓存"""
        self._realtime_opportunities.clear()

    def is_realtime_monitoring_active(self) -> bool:
        """检查实时监控是否活跃"""
        return self._ws_running and self._ws_client and self._ws_client.is_connected()

    async def stop_realtime_monitoring(self):
        """停止 WebSocket 实时监控"""
        if self._ws_client:
            self._ws_running = False
            await self._ws_client.disconnect()
            self.logger.info("实时监控已停止")
