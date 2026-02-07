"""
NegRisk 套利监控模块
检测多选项市场的套利机会（Σ(prices) ≠ 100%）

参考论文: "NegRisk Arbitrage: $28.99M Extraction (Apr 2024-Apr 2025)"
- NegRisk 市场有 3+ 个选项
- 当所有选项价格总和 < 100% 时存在套利机会
- 历史收益是单条件套利的 29 倍
"""

import logging
import json
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class NegRiskArbitrageType(Enum):
    """NegRisk 套利类型"""
    OVERPRICED = "overpriced"    # Σ(prices) > 100%: 卖空所有选项
    UNDERPRICED = "underpriced"  # Σ(prices) < 100%: 买入所有选项


@dataclass
class NegRiskOpportunity:
    """NegRisk 套利机会"""
    market_id: str
    market_name: str

    # 套利信息
    total_price: float           # 所有选项价格总和
    arbitrage_percent: float     # 套利空间百分比
    arbitrage_type: NegRiskArbitrageType

    # 选项详情
    outcomes: List[Dict]         # [{'name': 'Yes', 'price': 0.30}, ...]

    # 推荐操作
    action: str                  # "买入所有选项" 或 "卖空所有选项"
    expected_profit: float       # 预期收益率（扣除手续费后）

    timestamp: float


class NegRiskMonitor:
    """
    NegRisk 套利监控器
    检测多选项市场的定价偏差
    """

    def __init__(self, config: Dict):
        self.config = config
        self.logger = logging.getLogger(__name__)

        # 配置参数
        negrisk_config = config.get('negrisk', {})
        self.min_arbitrage_threshold = negrisk_config.get('min_arbitrage_threshold', 2.0)  # 默认 2%
        self.min_outcomes = negrisk_config.get('min_outcomes', 3)  # 至少 3 个选项
        self.max_outcomes = negrisk_config.get('max_outcomes', 10)  # 最多 10 个选项

        # 手续费（假设每笔交易 0.5%）
        self.trading_fee = negrisk_config.get('trading_fee', 0.005)

        # 统计信息
        self.total_scans = 0
        self.opportunities_found = 0

    def scan_market(self, market: Dict) -> Optional[NegRiskOpportunity]:
        """
        扫描单个市场寻找 NegRisk 套利机会

        Args:
            market: 市场数据（来自 Polymarket Gamma API）

        Returns:
            NegRisk 套利机会或 None
        """
        try:
            # 解析 outcomePrices
            outcome_prices_str = market.get('outcomePrices', '[]')
            if isinstance(outcome_prices_str, str):
                outcome_prices = json.loads(outcome_prices_str)
            else:
                outcome_prices = outcome_prices_str

            # 至少需要 min_outcomes 个选项
            if len(outcome_prices) < self.min_outcomes:
                return None

            # 限制选项数量（避免过于复杂的市场）
            if len(outcome_prices) > self.max_outcomes:
                return None

            # 获取选项名称
            outcomes_data = market.get('outcomes', [])
            if not outcomes_data:
                # 如果没有 outcomes 字段，生成默认名称
                outcome_names = [f"Option {i+1}" for i in range(len(outcome_prices))]
            else:
                outcome_names = [o.get('name', f"Option {i+1}") for i, o in enumerate(outcomes_data)]

            # 构建选项列表
            outcomes = []
            total_price = 0.0

            for i, price in enumerate(outcome_prices):
                price_float = float(price)
                if price_float <= 0:
                    return None  # 无效价格

                outcomes.append({
                    'name': outcome_names[i] if i < len(outcome_names) else f"Option {i+1}",
                    'price': price_float
                })
                total_price += price_float

            # 检查套利机会
            # 总价格应该接近 1.00 (100%)
            deviation = abs(1.0 - total_price) * 100  # 偏差百分比

            if deviation < self.min_arbitrage_threshold:
                return None  # 偏差太小，不构成套利机会

            # 确定套利类型
            if total_price > 1.0:
                arb_type = NegRiskArbitrageType.OVERPRICED
                action = "卖空所有选项"
                # 卖空所有选项的收益 = total_price - 1.0 - fees
                expected_profit = (total_price - 1.0) - (self.trading_fee * len(outcomes))
            else:
                arb_type = NegRiskArbitrageType.UNDERPRICED
                action = "买入所有选项"
                # 买入所有选项的收益 = 1.0 - total_price - fees
                expected_profit = (1.0 - total_price) - (self.trading_fee * len(outcomes))

            # 扣除手续费后仍需有利润
            if expected_profit <= 0:
                return None

            # 获取市场信息
            market_id = (market.get('conditionId') or
                        market.get('condition_id') or
                        market.get('questionId') or
                        market.get('question_id', ''))

            market_name = (market.get('question') or
                          market.get('title') or
                          market.get('description') or
                          f"Market {market_id[:8]}")

            opportunity = NegRiskOpportunity(
                market_id=market_id,
                market_name=market_name,
                total_price=total_price * 100,  # 转换为百分比显示
                arbitrage_percent=round(deviation, 2),
                arbitrage_type=arb_type,
                outcomes=[{
                    'name': o['name'],
                    'price': round(o['price'] * 100, 2)  # 转换为百分比
                } for o in outcomes],
                action=action,
                expected_profit=round(expected_profit * 100, 2),  # 转换为百分比
                timestamp=market.get('timestamp', 0)
            )

            self.opportunities_found += 1
            self.logger.info(
                f"发现 NegRisk 套利: {market_name[:50]}... "
                f"Σ={total_price:.4f} ({deviation:.2f}%) "
                f"选项数={len(outcomes)}"
            )

            return opportunity

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            self.logger.debug(f"解析市场数据失败: {e}")
            return None
        except Exception as e:
            self.logger.error(f"扫描 NegRisk 套利时出错: {type(e).__name__}: {e}")
            return None

    def scan_all_markets(self, markets: List[Dict]) -> List[NegRiskOpportunity]:
        """
        扫描所有市场寻找 NegRisk 套利机会

        Args:
            markets: 市场列表

        Returns:
            NegRisk 套利机会列表
        """
        self.total_scans += 1
        opportunities = []

        for market in markets:
            try:
                opp = self.scan_market(market)
                if opp:
                    opportunities.append(opp)
            except Exception as e:
                self.logger.error(f"扫描市场时出错: {type(e).__name__}: {e}")

        # 按套利空间降序排序
        opportunities.sort(key=lambda x: x.arbitrage_percent, reverse=True)

        self.logger.info(
            f"NegRisk 扫描完成: 扫描了 {len(markets)} 个市场，"
            f"发现 {len(opportunities)} 个套利机会"
        )

        return opportunities

    def calculate_position_sizes(self, opportunity: NegRiskOpportunity,
                                 total_investment: float = 1000) -> Dict[str, float]:
        """
        计算每个选项的头寸大小

        Args:
            opportunity: NegRisk 套利机会
            total_investment: 总投资金额（美元）

        Returns:
            每个选项的头寸大小 {'option_name': amount}
        """
        if opportunity.arbitrage_type == NegRiskArbitrageType.UNDERPRICED:
            # 买入所有选项：按价格比例分配
            positions = {}
            for outcome in opportunity.outcomes:
                price = outcome['price'] / 100  # 转回小数
                # 买入金额 = 总投资 × 价格比例
                positions[outcome['name']] = total_investment * price
            return positions

        else:  # OVERPRICED
            # 卖空所有选项：等额卖空
            num_outcomes = len(opportunity.outcomes)
            position_size = total_investment / num_outcomes
            return {o['name']: position_size for o in opportunity.outcomes}

    def get_statistics(self) -> Dict:
        """获取统计信息"""
        return {
            'total_scans': self.total_scans,
            'opportunities_found': self.opportunities_found,
            'min_arbitrage_threshold': self.min_arbitrage_threshold,
            'min_outcomes': self.min_outcomes
        }


def format_opportunity(opp: NegRiskOpportunity) -> str:
    """格式化 NegRisk 套利机会为可读字符串"""
    lines = [
        f"🎯 NegRisk 套利机会: {opp.market_name[:60]}",
        f"   总价格: {opp.total_price:.2f}%",
        f"   套利空间: {opp.arbitrage_percent:.2f}%",
        f"   操作: {opp.action}",
        f"   预期收益: {opp.expected_profit:.2f}%",
        f"   选项数: {len(opp.outcomes)}",
        "   选项详情:"
    ]

    for outcome in opp.outcomes:
        lines.append(f"     - {outcome['name']}: {outcome['price']:.2f}%")

    return "\n".join(lines)


def create_negrisk_monitor(config: Dict) -> NegRiskMonitor:
    """
    创建 NegRisk 监控器

    Args:
        config: 配置字典

    Returns:
        NegRiskMonitor 实例
    """
    return NegRiskMonitor(config)
