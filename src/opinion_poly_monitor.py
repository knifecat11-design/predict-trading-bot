"""
Opinion.trade ↔ Polymarket 跨平台套利监控
最小利润阈值: 2%
"""

import time
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from src.polymarket_api import RealPolymarketClient
from src.opinion_api import create_opinion_client

logger = logging.getLogger(__name__)


class OpinionPolyArbitrageType(Enum):
    """Opinion-Polymarket 套利类型"""
    POLY_YES_OPINION_NO = "poly_yes_opinion_no"
    OPINION_YES_POLY_NO = "opinion_yes_poly_no"


@dataclass
class OpinionPolyOpportunity:
    """Opinion-Polymarket 套利机会"""
    arbitrage_type: OpinionPolyArbitrageType
    market_name: str

    # 利差信息
    combined_price: float
    arbitrage_percent: float

    # Polymarket 信息
    poly_yes_price: float
    poly_no_price: float
    poly_action: str

    # Opinion 信息
    opinion_yes_price: float
    opinion_no_price: float
    opinion_action: str

    # 市场信息
    poly_market_id: str
    opinion_market_id: str
    match_confidence: float

    timestamp: float


class OpinionPolyMonitor:
    """
    Opinion ↔ Polymarket 套利监控器
    """

    def __init__(self, config: Dict):
        self.config = config
        self.logger = logging.getLogger(__name__)

        # 配置参数 - 2% 最小套利阈值
        arb_config = config.get('opinion_poly', {})
        self.min_arbitrage_threshold = arb_config.get('min_arbitrage_threshold', 2.0)
        self.min_confidence = arb_config.get('min_confidence', 0.2)

        # 统计信息
        self.total_scans = 0
        self.opportunities_found = 0

    def find_matching_markets(self,
                             poly_client,
                             opinion_client) -> List[Tuple[Dict, Dict, float]]:
        """
        查找 Opinion 和 Polymarket 之间的相似市场
        """
        self.logger.info("开始匹配 Opinion 和 Polymarket 市场...")

        # 获取市场列表
        poly_markets = poly_client.get_all_markets(limit=200, active_only=True)
        opinion_markets = opinion_client.get_markets(status='activated', sort_by=5, limit=200)

        self.logger.info(f"Polymarket: {len(poly_markets)} 个市场")
        self.logger.info(f"Opinion: {len(opinion_markets)} 个市场")

        # 简单关键词匹配
        matches = []
        for poly_market in poly_markets:
            poly_title = poly_market.get('question', '').lower()
            poly_keywords = self._extract_keywords(poly_title)

            for opinion_market in opinion_markets:
                opinion_title = opinion_market.get('marketTitle', '').lower()
                opinion_keywords = self._extract_keywords(opinion_title)

                # 计算相似度
                similarity = self._calculate_similarity(poly_keywords, opinion_keywords)

                if similarity >= self.min_confidence:
                    matches.append((poly_market, opinion_market, similarity))

        # 按置信度排序
        matches.sort(key=lambda x: x[2], reverse=True)

        self.logger.info(f"找到 {len(matches)} 对匹配市场")
        return matches[:50]

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

    def check_arbitrage(self,
                       poly_yes_price: float,
                       opinion_no_price: float,
                       market_name: str) -> Optional[float]:
        """
        检查套利机会：Poly Yes + Opinion No
        """
        # 确保价格在合理范围内
        poly_yes_price = max(0.01, min(0.99, poly_yes_price))
        opinion_no_price = max(0.01, min(0.99, opinion_no_price))

        # 计算组合价格
        combined = poly_yes_price + opinion_no_price

        # 计算套利空间
        arbitrage = (1.0 - combined) * 100

        if arbitrage >= self.min_arbitrage_threshold:
            return arbitrage

        return None

    def scan_opinion_poly_arbitrage(self,
                                    poly_client,
                                    opinion_client) -> List[OpinionPolyOpportunity]:
        """
        扫描 Opinion ↔ Polymarket 套利机会
        """
        self.total_scans += 1
        opportunities = []

        # 查找匹配的市场
        matches = self.find_matching_markets(poly_client, opinion_client)

        for poly_market, opinion_market, confidence in matches:
            try:
                # 获取 Polymarket 价格（使用 bestBid/bestAsk）
                best_bid = poly_market.get('bestBid')
                best_ask = poly_market.get('bestAsk')

                if best_bid is not None and best_ask is not None:
                    # 使用真实订单簿价格
                    poly_yes_ask = float(best_ask)  # 买 Yes 的价格
                    poly_no_bid = float(best_bid)    # 买 No 的价格
                    poly_yes_price = (best_bid + best_ask) / 2
                    poly_no_price = 1.0 - poly_yes_price
                else:
                    # 回退：使用 outcomePrices
                    import json
                    outcome_prices_str = poly_market.get('outcomePrices', '[]')
                    if isinstance(outcome_prices_str, str):
                        outcome_prices = json.loads(outcome_prices_str)
                    else:
                        outcome_prices = outcome_prices_str

                    if len(outcome_prices) < 2:
                        continue

                    self.logger.debug(f"使用 outcomePrices（不准确）")
                    poly_yes_price = float(outcome_prices[0])
                    poly_no_price = float(outcome_prices[1])
                    spread = max(0.01, poly_yes_price * 0.02)
                    poly_yes_ask = poly_yes_price + spread / 2
                    poly_no_bid = poly_no_price - spread / 2

                # 获取 Opinion 价格
                opinion_market_info = opinion_client.get_market_info(opinion_market.get('marketId'))

                if not opinion_market_info:
                    continue

                opinion_yes_price = opinion_market_info.yes_price
                opinion_no_price = opinion_market_info.no_price

                # 检查方向 1: 买 Poly Yes + 买 Opinion No
                arb1 = self.check_arbitrage(poly_yes_ask, opinion_no_price, poly_market.get('question', ''))
                if arb1 is not None:
                    opportunities.append(OpinionPolyOpportunity(
                        arbitrage_type=OpinionPolyArbitrageType.POLY_YES_OPINION_NO,
                        market_name=poly_market.get('question', '')[:60],
                        combined_price=(poly_yes_ask + opinion_no_price) * 100,
                        arbitrage_percent=round(arb1, 2),
                        poly_yes_price=poly_yes_ask * 100,
                        poly_no_price=poly_no_price * 100,
                        poly_action="买Yes",
                        opinion_yes_price=opinion_yes_price * 100,
                        opinion_no_price=opinion_no_price * 100,
                        opinion_action="买No",
                        poly_market_id=poly_market.get('conditionId', poly_market.get('condition_id', '')),
                        opinion_market_id=str(opinion_market.get('marketId', '')),
                        match_confidence=round(confidence, 2),
                        timestamp=time.time()
                    ))
                    self.opportunities_found += 1

                # 检查方向 2: 买 Opinion Yes + 买 Poly No
                arb2 = self.check_arbitrage(opinion_yes_price, poly_no_bid, opinion_market.get('marketTitle', ''))
                if arb2 is not None:
                    opportunities.append(OpinionPolyOpportunity(
                        arbitrage_type=OpinionPolyArbitrageType.OPINION_YES_POLY_NO,
                        market_name=opinion_market.get('marketTitle', '')[:60],
                        combined_price=(opinion_yes_price + poly_no_bid) * 100,
                        arbitrage_percent=round(arb2, 2),
                        poly_yes_price=poly_yes_price * 100,
                        poly_no_price=poly_no_bid * 100,
                        poly_action="买No",
                        opinion_yes_price=opinion_yes_price * 100,
                        opinion_no_price=opinion_no_price * 100,
                        opinion_action="买Yes",
                        poly_market_id=poly_market.get('conditionId', poly_market.get('condition_id', '')),
                        opinion_market_id=str(opinion_market.get('marketId', '')),
                        match_confidence=round(confidence, 2),
                        timestamp=time.time()
                    ))
                    self.opportunities_found += 1

            except Exception as e:
                self.logger.error(f"扫描市场时出错: {type(e).__name__}: {e}")
                continue

        # 按套利空间排序
        opportunities.sort(key=lambda x: x.arbitrage_percent, reverse=True)

        self.logger.info(f"扫描完成: 发现 {len(opportunities)} 个套利机会")
        return opportunities

    def get_statistics(self) -> Dict:
        """获取统计信息"""
        return {
            'total_scans': self.total_scans,
            'opportunities_found': self.opportunities_found,
            'min_arbitrage_threshold': self.min_arbitrage_threshold
        }


def format_opportunity(opp: OpinionPolyOpportunity) -> str:
    """格式化套利机会为可读字符串"""
    lines = [
        f"🔄 跨平台套利: {opp.market_name}",
        f"   类型: {opp.arbitrage_type.value}",
        f"   套利空间: {opp.arbitrage_percent:.2f}%",
        f"   组合价格: {opp.combined_price:.2f}%",
        f"",
        f"   Polymarket:",
        f"     Yes: {opp.poly_yes_price:.2f}¢  No: {opp.poly_no_price:.2f}¢",
        f"     操作: {opp.poly_action}",
        f"",
        f"   Opinion:",
        f"     Yes: {opp.opinion_yes_price:.2f}¢  No: {opp.opinion_no_price:.2f}¢",
        f"     操作: {opp.opinion_action}",
        f"",
        f"   匹配置信度: {opp.match_confidence:.2f}"
    ]

    return "\n".join(lines)


def create_opinion_poly_monitor(config: Dict) -> OpinionPolyMonitor:
    """创建 Opinion-Polymarket 监控器"""
    return OpinionPolyMonitor(config)
