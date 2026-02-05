"""
套利监控模块
监控 Polymarket 和 Predict.fun 之间的套利机会
策略：Yes价格 + No价格 < 100% 时存在套利空间
"""

import time
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


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

    # Polymarket 信息
    poly_yes_price: float          # Polymarket Yes价格
    poly_no_price: float           # Polymarket No价格 (1 - yes_price)
    poly_action: str               # "买Yes" 或 "买No"

    # Predict 信息
    predict_yes_price: float       # Predict Yes价格
    predict_no_price: float        # Predict No价格
    predict_action: str            # "买Yes" 或 "买No"

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

        self.market_map = self._load_market_map()
        self.logger = logging.getLogger(__name__)

        # 统计信息
        self.total_scans = 0
        self.opportunities_found = 0

    def _load_market_map(self) -> Dict[str, str]:
        """
        加载市场映射
        """
        return {
            "test-market-1": "test-market"
        }

    def check_arbitrage(self,
                       poly_yes_price: float,
                       predict_no_price: float,
                       market_name: str) -> Optional[ArbitrageOpportunity]:
        """
        检查套利机会：Polymarket Yes + Predict No

        Args:
            poly_yes_price: Polymarket Yes价格
            predict_no_price: Predict No价格
            market_name: 市场名称

        Returns:
            套利机会（如果存在）
        """
        # No价格转换为小数形式（如果输入是百分比如50，需要除以100）
        if predict_no_price > 1:
            predict_no_price = predict_no_price / 100

        # 计算组合价格
        combined = poly_yes_price + predict_no_price

        # 计算套利空间
        arbitrage = (1.0 - combined) * 100  # 转换为百分比

        # 检查是否满足阈值
        if arbitrage >= self.arb_config.min_arbitrage_percent:
            return ArbitrageOpportunity(
                arbitrage_type=ArbitrageType.POLY_YES_PREDICT_NO,
                market_name=market_name,
                combined_price=combined * 100,  # 转换为百分比显示
                arbitrage_percent=round(arbitrage, 2),
                poly_yes_price=poly_yes_price * 100,  # 转换为百分比显示
                poly_no_price=round((1 - poly_yes_price) * 100, 1),
                poly_action="买Yes",
                predict_yes_price=round((1 - predict_no_price) * 100, 1),
                predict_no_price=predict_no_price * 100,
                predict_action="买No",
                timestamp=time.time()
            )

        return None

    def check_reverse_arbitrage(self,
                               predict_yes_price: float,
                               poly_no_price: float,
                               market_name: str) -> Optional[ArbitrageOpportunity]:
        """
        检查反向套利机会：Predict Yes + Polymarket No

        Args:
            predict_yes_price: Predict Yes价格
            poly_no_price: Polymarket No价格
            market_name: 市场名称

        Returns:
            套利机会（如果存在）
        """
        # No价格转换
        if poly_no_price > 1:
            poly_no_price = poly_no_price / 100

        # 计算组合价格
        combined = predict_yes_price + poly_no_price

        # 计算套利空间
        arbitrage = (1.0 - combined) * 100

        # 检查是否满足阈值
        if arbitrage >= self.arb_config.min_arbitrage_percent:
            return ArbitrageOpportunity(
                arbitrage_type=ArbitrageType.PREDICT_YES_POLY_NO,
                market_name=market_name,
                combined_price=combined * 100,
                arbitrage_percent=round(arbitrage, 2),
                poly_yes_price=round((1 - poly_no_price) * 100, 1),
                poly_no_price=poly_no_price * 100,
                poly_action="买No",
                predict_yes_price=predict_yes_price * 100,
                predict_no_price=round((1 - predict_yes_price) * 100, 1),
                predict_action="买Yes",
                timestamp=time.time()
            )

        return None

    def scan_all_markets(self,
                        poly_client,
                        predict_client) -> List[ArbitrageOpportunity]:
        """
        扫描所有市场寻找套利机会

        Args:
            poly_client: Polymarket客户端
            predict_client: Predict客户端

        Returns:
            套利机会列表
        """
        self.total_scans += 1
        opportunities = []

        for poly_market_id, predict_market_id in self.market_map.items():
            try:
                # 获取市场数据
                poly_market = poly_client.get_market_info(poly_market_id)
                if not poly_market:
                    self.logger.warning(f"无法获取市场信息: {poly_market_id}")
                    continue

                poly_orderbook = poly_client.get_order_book(poly_market_id)
                if not poly_orderbook:
                    self.logger.warning(f"无法获取订单簿: {poly_market_id}")
                    continue

                predict_market = predict_client.get_market_data()
                if not predict_market:
                    self.logger.warning(f"无法获取 Predict 市场数据")
                    continue

                # 检查必要的属性是否存在
                if not all(hasattr(poly_market, attr) for attr in ['current_price', 'question_title']):
                    self.logger.error(f"poly_market 缺少必要属性")
                    continue

                if not hasattr(poly_orderbook, 'yes_bid'):
                    self.logger.error(f"poly_orderbook 缺少 yes_bid 属性")
                    continue

                if not all(hasattr(predict_market, attr) for attr in ['yes_bid', 'current_price']):
                    self.logger.error(f"predict_market 缺少必要属性")
                    continue

                # 方向1: Polymarket Yes + Predict No
                opp1 = self.check_arbitrage(
                    poly_market.current_price,
                    predict_market.yes_bid,  # Predict的No价格 = 1 - Yes_bid价格
                    poly_market.question_title
                )

                if opp1:
                    opportunities.append(opp1)
                    self.opportunities_found += 1
                    self.logger.info(f"发现套利机会: {opp1.market_name} (Poly Yes + Predict No)")

                # 方向2: Predict Yes + Polymarket No
                opp2 = self.check_reverse_arbitrage(
                    predict_market.current_price,
                    poly_orderbook.yes_bid,  # Poly的No价格 = 1 - Yes_bid价格
                    poly_market.question_title
                )

                if opp2:
                    opportunities.append(opp2)
                    self.opportunities_found += 1
                    self.logger.info(f"发现套利机会: {opp2.market_name} (Predict Yes + Poly No)")

            except AttributeError as e:
                self.logger.error(f"扫描市场 {poly_market_id} 时属性错误: {e}")
            except TypeError as e:
                self.logger.error(f"扫描市场 {poly_market_id} 时类型错误: {e}")
            except Exception as e:
                self.logger.error(f"扫描市场 {poly_market_id} 时未知错误: {type(e).__name__}: {e}", exc_info=True)

        return opportunities

    def get_statistics(self) -> Dict:
        """获取统计信息"""
        return {
            'total_scans': self.total_scans,
            'opportunities_found': self.opportunities_found,
            'min_arbitrage_threshold': self.arb_config.min_arbitrage_percent
        }
