"""
对冲套利监控模块
专门针对 Polymarket ↔ Predict.fun 的无损套利
核心目标：
1. 尽可能无损（对冲风险敞口）
2. 自动挂单获取 Predict.fun 积分
3. 跨平台套利检测
"""

import time
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class ArbitrageStrategy(Enum):
    """套利策略类型"""
    # 买 Yes @ 平台1 + 买 No @ 平台2 = 锁定利润
    HEDGED_YES_NO = "hedged_yes_no"

    # 挂单策略：在 Predict.fun 挂单赚取积分
    LIMIT_ORDER_POINTS = "limit_order_points"

    # 价差套利：利用两平台价差
    PRICE_DIFF = "price_diff"


@dataclass
class HedgedArbitrageOpportunity:
    """对冲套利机会"""
    strategy: ArbitrageStrategy

    # 市场信息
    market_name: str
    polymarket_id: str
    predict_id: Optional[str]

    # 价格信息
    poly_yes_price: float
    poly_no_price: float
    predict_yes_price: float
    predict_no_price: float

    # 套利计算
    combined_price: float          # 组合价格 (应该 < 1.0)
    arbitrage_percent: float       # 套利空间百分比
    expected_profit: float         # 预期利润（扣除手续费）

    # 推荐操作（对冲）
    action_poly: str               # "买 Yes" 或 "买 No" 或 "挂单"
    action_predict: str            # "买 Yes" 或 "买 No" 或 "挂单"

    # 订单建议
    poly_order_price: float        # Polymarket 下单价格
    poly_order_size: float         # Polymarket 下单数量
    predict_order_price: float     # Predict.fun 下单价格
    predict_order_size: float      # Predict.fun 下单数量

    # 风险指标
    exposure: float                # 净风险敞口（应该接近 0）
    risk_score: float              # 风险评分 (0-100, 越低越好)

    # 流动性检查
    poly_liquidity: float
    predict_liquidity: float

    timestamp: float


class HedgedArbitrageMonitor:
    """
    对冲套利监控器
    专注于无损、对冲的套利策略
    """

    def __init__(self, config: Dict):
        self.config = config
        arb_config = config.get('arbitrage', {})

        # 配置参数
        self.min_arbitrage_threshold = arb_config.get('min_arbitrage_threshold', 1.0)  # 最小套利空间 1%
        self.max_risk_exposure = arb_config.get('max_risk_exposure', 0.05)  # 最大风险敞口 5%
        self.trading_fee = arb_config.get('trading_fee', 0.005)  # 手续费 0.5%

        # Predict.fun 挂单策略配置
        self.predict_spread_percent = config.get('strategy', {}).get('spread_percent', 6.0)  # 挂单价差 ±6%
        self.predict_order_size = config.get('market', {}).get('base_position_size', 10)  # 基础仓位

        # 统计信息
        self.total_scans = 0
        self.opportunities_found = 0
        self.orders_placed = 0

    def calculate_hedged_arbitrage(self,
                                   poly_yes: float,
                                   poly_no: float,
                                   predict_yes: float,
                                   predict_no: float) -> Optional[HedgedArbitrageOpportunity]:
        """
        计算对冲套利机会

        策略：买 Poly Yes + 买 Predict No = 对冲头寸
        如果 poly_yes + predict_no < 1.0 - fee，则存在套利空间

        Args:
            poly_yes: Polymarket Yes 价格
            poly_no: Polymarket No 价格
            predict_yes: Predict.fun Yes 价格
            predict_no: Predict.fun No 价格

        Returns:
            套利机会或 None
        """
        # 策略 1: 买 Poly Yes + 买 Predict No
        combined_1 = poly_yes + predict_no
        profit_1 = 1.0 - combined_1 - self.trading_fee * 2  # 扣除两笔交易手续费

        if profit_1 > 0:
            arbitrage_percent = profit_1 * 100

            if arbitrage_percent >= self.min_arbitrage_threshold:
                # 计算风险敞口（理想情况应该接近 0）
                # 如果 Poly Yes 和 Predict No 的价格都正确，无论结果如何都能盈利
                exposure = abs(poly_yes - (1 - predict_no))

                # 风险评分：基于价格偏离度
                risk_score = min(100, exposure * 100)

                return HedgedArbitrageOpportunity(
                    strategy=ArbitrageStrategy.HEDGED_YES_NO,
                    market_name="",
                    polymarket_id="",
                    predict_id=None,
                    poly_yes_price=poly_yes,
                    poly_no_price=poly_no,
                    predict_yes_price=predict_yes,
                    predict_no_price=predict_no,
                    combined_price=combined_1,
                    arbitrage_percent=arbitrage_percent,
                    expected_profit=profit_1,
                    action_poly="买 Yes",
                    action_predict="买 No",
                    poly_order_price=poly_yes,
                    poly_order_size=100,  # 默认数量
                    predict_order_price=predict_no,
                    predict_order_size=100,
                    exposure=exposure,
                    risk_score=risk_score,
                    poly_liquidity=0,
                    predict_liquidity=0,
                    timestamp=time.time()
                )

        # 策略 2: 买 Predict Yes + 买 Poly No
        combined_2 = predict_yes + poly_no
        profit_2 = 1.0 - combined_2 - self.trading_fee * 2

        if profit_2 > 0:
            arbitrage_percent = profit_2 * 100

            if arbitrage_percent >= self.min_arbitrage_threshold:
                exposure = abs(predict_yes - (1 - poly_no))
                risk_score = min(100, exposure * 100)

                return HedgedArbitrageOpportunity(
                    strategy=ArbitrageStrategy.HEDGED_YES_NO,
                    market_name="",
                    polymarket_id="",
                    predict_id=None,
                    poly_yes_price=poly_yes,
                    poly_no_price=poly_no,
                    predict_yes_price=predict_yes,
                    predict_no_price=predict_no,
                    combined_price=combined_2,
                    arbitrage_percent=arbitrage_percent,
                    expected_profit=profit_2,
                    action_poly="买 No",
                    action_predict="买 Yes",
                    poly_order_price=poly_no,
                    poly_order_size=100,
                    predict_order_price=predict_yes,
                    predict_order_size=100,
                    exposure=exposure,
                    risk_score=risk_score,
                    poly_liquidity=0,
                    predict_liquidity=0,
                    timestamp=time.time()
                )

        return None

    def scan_for_hedged_arbitrage(self,
                                  poly_client,
                                  predict_client) -> List[HedgedArbitrageOpportunity]:
        """
        扫描对冲套利机会

        Args:
            poly_client: Polymarket 客户端
            predict_client: Predict.fun 客户端

        Returns:
            对冲套利机会列表
        """
        self.total_scans += 1
        opportunities = []

        logger.info("开始扫描对冲套利机会...")

        # 获取 Polymarket 市场
        poly_markets = poly_client.get_all_markets(limit=100, active_only=True)
        logger.info(f"Polymarket: {len(poly_markets)} 个市场")

        # 获取 Predict.fun 市场
        predict_markets = predict_client.get_markets(status='open', sort='popular', limit=100)
        logger.info(f"Predict.fun: {len(predict_markets)} 个市场")

        # 简单匹配：按关键词相似度
        matched_pairs = self._match_markets(poly_markets, predict_markets)
        logger.info(f"找到 {len(matched_pairs)} 对匹配市场")

        for poly_market, predict_market, confidence in matched_pairs:
            try:
                # 获取 Polymarket 价格
                import json
                outcome_prices_str = poly_market.get('outcomePrices', '[]')
                if isinstance(outcome_prices_str, str):
                    outcome_prices = json.loads(outcome_prices_str)
                else:
                    outcome_prices = outcome_prices_str

                if len(outcome_prices) < 2:
                    continue

                poly_yes = float(outcome_prices[0])
                poly_no = float(outcome_prices[1])

                # 获取 Predict.fun 订单簿价格（重要：使用 orderBook，而不是 price）
                orderbook = predict_market.get('orderBook', {})
                bids = orderbook.get('bids', [])
                asks = orderbook.get('asks', [])

                if bids and asks:
                    # 使用订单簿的实际可成交价格
                    predict_yes_bid = float(bids[0].get('price', 0.49))  # 买一价
                    predict_yes_ask = float(asks[0].get('price', 0.51))  # 卖一价
                    predict_yes = (predict_yes_bid + predict_yes_ask) / 2  # 中间价用于参考
                else:
                    # 回退到 price 字段（中间价，可能不准确）
                    logger.warning(f"市场 {predict_market.get('id')} 没有订单簿数据")
                    predict_price = predict_market.get('price', 0.5)
                    if isinstance(predict_price, str):
                        predict_price = float(predict_price)
                    predict_yes = predict_price

                predict_no = 1.0 - predict_yes

                # 检查套利机会（使用订单簿价格）
                opp = self.calculate_hedged_arbitrage(
                    poly_yes, poly_no, predict_yes, predict_no
                )

                if opp:
                    # 填充市场信息
                    opp.market_name = poly_market.get('question', '')[:60]
                    opp.polymarket_id = poly_market.get('conditionId', poly_market.get('condition_id', ''))
                    opp.predict_id = predict_market.get('id', predict_market.get('market_id', ''))
                    opp.poly_liquidity = float(poly_market.get('liquidity', 0) or 0)
                    opp.predict_liquidity = float(predict_market.get('liquidity', 0) or 0)

                    opportunities.append(opp)
                    self.opportunities_found += 1

                    logger.info(
                        f"发现对冲套利: {opp.market_name[:40]}... "
                        f"利润={opp.arbitrage_percent:.2f}% "
                        f"风险={opp.risk_score:.1f}"
                    )

            except Exception as e:
                logger.error(f"分析市场时出错: {type(e).__name__}: {e}")
                continue

        # 按套利空间排序
        opportunities.sort(key=lambda x: x.arbitrage_percent, reverse=True)

        logger.info(f"扫描完成: 发现 {len(opportunities)} 个对冲套利机会")

        return opportunities

    def _match_markets(self,
                      poly_markets: List[Dict],
                      predict_markets: List[Dict]) -> List[Tuple[Dict, Dict, float]]:
        """
        匹配 Polymarket 和 Predict.fun 市场

        Args:
            poly_markets: Polymarket 市场列表
            predict_markets: Predict.fun 市场列表

        Returns:
            [(poly_market, predict_market, confidence), ...]
        """
        matches = []

        for poly_market in poly_markets:
            poly_title = poly_market.get('question', '').lower()
            poly_keywords = self._extract_keywords(poly_title)

            for predict_market in predict_markets:
                predict_title = (
                    predict_market.get('question') or
                    predict_market.get('title') or
                    predict_market.get('name', '')
                ).lower()

                predict_keywords = self._extract_keywords(predict_title)

                # 计算相似度
                similarity = self._calculate_similarity(poly_keywords, predict_keywords)

                if similarity >= 0.3:  # 最低相似度阈值
                    matches.append((poly_market, predict_market, similarity))

        # 按相似度排序
        matches.sort(key=lambda x: x[2], reverse=True)

        return matches[:50]  # 最多返回 50 对

    def _extract_keywords(self, title: str) -> set:
        """从标题中提取关键词"""
        import re

        stop_words = {'will', 'the', 'a', 'an', 'be', 'by', 'in', 'on', 'at', 'to', 'for', 'of'}
        words = re.findall(r'\b\w+\b', title.lower())
        keywords = {w for w in words if len(w) > 2 and w not in stop_words}

        return keywords

    def _calculate_similarity(self, keywords1: set, keywords2: set) -> float:
        """计算关键词集合的相似度"""
        if not keywords1 or not keywords2:
            return 0.0

        intersection = keywords1 & keywords2
        union = keywords1 | keywords2

        return len(intersection) / len(union) if union else 0.0

    def generate_limit_order_strategy(self,
                                     predict_client,
                                     poly_yes_price: float) -> Optional[Dict]:
        """
        生成 Predict.fun 挂单策略（获取积分）

        策略：在当前价格两侧挂限价单
        - 买一价 = 当前价格 - spread%
        - 卖一价 = 当前价格 + spread%

        Args:
            predict_client: Predict.fun 客户端
            poly_yes_price: Polymarket Yes 价格（作为参考）

        Returns:
            挂单策略或 None
        """
        # 根据 Polymarket 价格确定 Predict.fun 挂单价格
        # 如果 Poly Yes 更便宜，在 Predict.fun 挂更高的买单价
        # 如果 Poly Yes 更贵，在 Predict.fun 挂更低的卖单价

        spread = self.predict_spread_percent / 100  # 转换为小数

        # 策略 1: 如果 Poly Yes 低于 Predict Yes，在 Predict 挂买单
        if poly_yes_price < 0.5:
            # 在 Predict.fun 挂买单（低于当前市价）
            limit_buy_price = max(0.01, poly_yes_price * (1 + spread * 0.5))
            return {
                'action': '挂买单',
                'price': limit_buy_price,
                'size': self.predict_order_size,
                'reason': f'Poly Yes 较低 ({poly_yes_price:.2f})，在 Predict 挂买单赚取积分'
            }

        # 策略 2: 如果 Poly Yes 高于 Predict Yes，在 Predict 挂卖单
        else:
            # 在 Predict.fun 挂卖单（高于当前市价）
            limit_sell_price = min(0.99, poly_yes_price * (1 - spread * 0.5))
            return {
                'action': '挂卖单',
                'price': limit_sell_price,
                'size': self.predict_order_size,
                'reason': f'Poly Yes 较高 ({poly_yes_price:.2f})，在 Predict 挂卖单赚取积分'
            }

    def execute_hedged_trade(self,
                            opportunity: HedgedArbitrageOpportunity,
                            poly_client,
                            predict_client) -> bool:
        """
        执行对冲交易

        Args:
            opportunity: 套利机会
            poly_client: Polymarket 客户端
            predict_client: Predict.fun 客户端

        Returns:
            是否成功执行
        """
        logger.info(f"执行对冲交易: {opportunity.market_name}")
        logger.info(f"  Polymarket: {opportunity.action_poly} @ {opportunity.poly_order_price:.2f}")
        logger.info(f"  Predict.fun: {opportunity.action_predict} @ {opportunity.predict_order_price:.2f}")
        logger.info(f"  预期利润: {opportunity.arbitrage_percent:.2f}%")
        logger.info(f"  风险敞口: {opportunity.exposure:.4f}")

        # TODO: 实现实际的交易逻辑
        # 1. 在 Polymarket 下单（需要交易 API）
        # 2. 在 Predict.fun 下单
        # 3. 监控订单状态
        # 4. 确认对冲完成

        logger.warning("交易执行尚未实现，仅模拟")
        return False

    def get_statistics(self) -> Dict:
        """获取统计信息"""
        return {
            'total_scans': self.total_scans,
            'opportunities_found': self.opportunities_found,
            'orders_placed': self.orders_placed,
            'min_arbitrage_threshold': self.min_arbitrage_threshold,
            'max_risk_exposure': self.max_risk_exposure
        }


def format_hedged_opportunity(opp: HedgedArbitrageOpportunity) -> str:
    """格式化对冲套利机会为可读字符串"""
    lines = [
        f"🎯 对冲套利机会: {opp.market_name}",
        f"   策略: {opp.strategy.value}",
        f"   套利空间: {opp.arbitrage_percent:.2f}%",
        f"   组合价格: {opp.combined_price:.4f}",
        f"   预期利润: {opp.expected_profit:.4f}",
        f"",
        f"   Polymarket:",
        f"     Yes: {opp.poly_yes_price:.2f}¢  No: {opp.poly_no_price:.2f}¢",
        f"     操作: {opp.action_poly} @ {opp.poly_order_price:.2f}¢",
        f"",
        f"   Predict.fun:",
        f"     Yes: {opp.predict_yes_price:.2f}¢  No: {opp.predict_no_price:.2f}¢",
        f"     操作: {opp.action_predict} @ {opp.predict_order_price:.2f}¢",
        f"",
        f"   风险分析:",
        f"     净敞口: {opp.exposure:.4f} (越接近 0 越好)",
        f"     风险评分: {opp.risk_score:.1f}/100 (越低越好)",
        f"     Poly 流动性: ${opp.poly_liquidity:,.0f}",
        f"     Predict 流动性: ${opp.predict_liquidity:,.0f}"
    ]

    return "\n".join(lines)


def create_hedged_arbitrage_monitor(config: Dict) -> HedgedArbitrageMonitor:
    """
    创建对冲套利监控器

    Args:
        config: 配置字典

    Returns:
        HedgedArbitrageMonitor 实例
    """
    return HedgedArbitrageMonitor(config)
