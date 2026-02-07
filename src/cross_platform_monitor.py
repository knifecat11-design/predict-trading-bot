"""
跨平台套利监控模块
监控 Polymarket ↔ Kalshi 之间的套利机会
"""

import logging
import time
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

from .polymarket_api import RealPolymarketClient
from .kalshi_api import KalshiAPIClient


logger = logging.getLogger(__name__)


class CrossPlatformArbitrageType(Enum):
    """跨平台套利类型"""
    POLY_YES_KALSHI_NO = "poly_yes_kalshi_no"    # Poly买Yes + Kalshi买No
    KALSHI_YES_POLY_NO = "kalshi_yes_poly_no"    # Kalshi买Yes + Poly买No


@dataclass
class CrossPlatformOpportunity:
    """跨平台套利机会"""
    arbitrage_type: CrossPlatformArbitrageType
    market_name: str

    # 利差信息
    combined_price: float
    arbitrage_percent: float

    # Polymarket 信息
    poly_yes_price: float
    poly_no_price: float
    poly_action: str

    # Kalshi 信息
    kalshi_yes_price: float
    kalshi_no_price: float
    kalshi_action: str

    # 市场信息
    poly_market_id: str
    kalshi_market_id: str
    match_confidence: float

    timestamp: float


class CrossPlatformMonitor:
    """
    跨平台套利监控器
    监控 Polymarket 和 Kalshi 之间的价格差异
    """

    def __init__(self, config: Dict):
        self.config = config
        self.logger = logging.getLogger(__name__)

        # 配置参数
        arb_config = config.get('cross_platform', {})
        self.min_arbitrage_threshold = arb_config.get('min_arbitrage_threshold', 2.0)  # 默认 2%

        # 市场匹配参数
        self.min_confidence = arb_config.get('min_confidence', 0.3)

        # 统计信息
        self.total_scans = 0
        self.opportunities_found = 0

    def find_matching_markets(self,
                             poly_client: RealPolymarketClient,
                             kalshi_client: KalshiAPIClient) -> List[Dict]:
        """
        查找 Polymarket 和 Kalshi 之间的相似市场

        Args:
            poly_client: Polymarket 客户端
            kalshi_client: Kalshi 客户端

        Returns:
            匹配的市场列表 [{'poly_market': {...}, 'kalshi_market': {...}, 'confidence': 0.8}]
        """
        matches = []

        # 获取市场列表
        poly_markets = poly_client.get_all_markets(limit=200, active_only=True)
        kalshi_markets = kalshi_client.get_markets(limit=200, status='open')

        self.logger.info(f"Polymarket: {len(poly_markets)} 个市场")
        self.logger.info(f"Kalshi: {len(kalshi_markets)} 个市场")

        # 简单关键词匹配
        for poly_market in poly_markets:
            poly_title = poly_market.get('question', '').lower()

            # 提取关键词
            poly_keywords = self._extract_keywords(poly_title)

            for kalshi_market in kalshi_markets:
                kalshi_title = kalshi_market.get('title', kalshi_market.get('question', '')).lower()

                # 计算相似度
                kalshi_keywords = self._extract_keywords(kalshi_title)
                similarity = self._calculate_similarity(poly_keywords, kalshi_keywords)

                if similarity >= self.min_confidence:
                    matches.append({
                        'poly_market': poly_market,
                        'kalshi_market': kalshi_market,
                        'confidence': similarity,
                        'poly_title': poly_market.get('question', ''),
                        'kalshi_title': kalshi_market.get('title', kalshi_market.get('question', ''))
                    })

        # 按置信度排序
        matches.sort(key=lambda x: x['confidence'], reverse=True)

        self.logger.info(f"找到 {len(matches)} 对匹配市场")
        return matches[:50]  # 最多返回 50 对

    def _extract_keywords(self, title: str) -> set:
        """从标题中提取关键词"""
        import re

        # 移除停用词
        stop_words = {'will', 'the', 'a', 'an', 'be', 'by', 'in', 'on', 'at', 'to', 'for', 'of'}

        # 提取单词
        words = re.findall(r'\b\w+\b', title.lower())

        # 过滤停用词和短词
        keywords = {w for w in words if len(w) > 2 and w not in stop_words}

        return keywords

    def _calculate_similarity(self, keywords1: set, keywords2: set) -> float:
        """计算两个关键词集合的相似度"""
        if not keywords1 or not keywords2:
            return 0.0

        intersection = keywords1 & keywords2
        union = keywords1 | keywords2

        return len(intersection) / len(union) if union else 0.0

    def check_arbitrage(self,
                       poly_yes_price: float,
                       kalshi_no_price: float,
                       market_name: str) -> Optional[float]:
        """
        检查套利机会：Poly Yes + Kalshi No

        Args:
            poly_yes_price: Polymarket Yes 价格
            kalshi_no_price: Kalshi No 价格
            market_name: 市场名称

        Returns:
            套利百分比或 None
        """
        # 确保价格在合理范围内
        poly_yes_price = max(0.01, min(0.99, poly_yes_price))
        kalshi_no_price = max(0.01, min(0.99, kalshi_no_price))

        # 计算组合价格
        combined = poly_yes_price + kalshi_no_price

        # 计算套利空间
        arbitrage = (1.0 - combined) * 100

        if arbitrage >= self.min_arbitrage_threshold:
            return arbitrage

        return None

    def scan_cross_platform_arbitrage(self,
                                     poly_client: RealPolymarketClient,
                                     kalshi_client: KalshiAPIClient) -> List[CrossPlatformOpportunity]:
        """
        扫描跨平台套利机会

        Args:
            poly_client: Polymarket 客户端
            kalshi_client: Kalshi 客户端

        Returns:
            跨平台套利机会列表
        """
        self.total_scans += 1
        opportunities = []

        # 查找匹配的市场
        matches = self.find_matching_markets(poly_client, kalshi_client)

        for match in matches:
            try:
                poly_market = match['poly_market']
                kalshi_market = match['kalshi_market']

                # 获取 Polymarket 订单簿价格（重要：使用 bestBid/bestAsk）
                # 买 Yes 用 bestAsk（卖一价），买 No 用 bestBid（买一价）
                best_bid = poly_market.get('bestBid')
                best_ask = poly_market.get('bestAsk')

                if best_bid is not None and best_ask is not None:
                    # 使用真实订单簿价格
                    poly_yes_ask = float(best_ask)  # 买 Yes 的价格（卖一价）
                    poly_no_bid = float(best_bid)    # 买 No 的价格（买一价，等于买 Yes 的反向）
                    poly_yes_price = (best_bid + best_ask) / 2  # 中间价仅用于参考
                    poly_no_price = 1.0 - poly_yes_price
                else:
                    # 回退：使用 outcomePrices（中间价，不准确）
                    import json
                    outcome_prices_str = poly_market.get('outcomePrices', '[]')
                    if isinstance(outcome_prices_str, str):
                        outcome_prices = json.loads(outcome_prices_str)
                    else:
                        outcome_prices = outcome_prices_str

                    if len(outcome_prices) < 2:
                        continue

                    self.logger.debug(f"市场 {poly_market.get('question', '')[:30]}... 没有 bestBid/bestAsk，使用 outcomePrices（可能不准确）")
                    poly_yes_price = float(outcome_prices[0])
                    poly_no_price = float(outcome_prices[1])
                    # 为中间价添加价差估算
                    spread = max(0.01, poly_yes_price * 0.02)
                    poly_yes_ask = poly_yes_price + spread / 2  # 估算卖一价
                    poly_no_bid = poly_no_price - spread / 2     # 估算买一价

                # 获取 Kalshi 价格
                kalshi_yes_price = float(kalshi_market.get('yes_price', 0.5))
                kalshi_no_price = 1.0 - kalshi_yes_price

                # 检查方向 1: 买 Poly Yes + 买 Kalshi No
                # 使用 poly_yes_ask（实际买入价格）
                arb1 = self.check_arbitrage(poly_yes_ask, kalshi_no_price, match['poly_title'])
                if arb1 is not None:
                    opportunities.append(CrossPlatformOpportunity(
                        arbitrage_type=CrossPlatformArbitrageType.POLY_YES_KALSHI_NO,
                        market_name=match['poly_title'][:60],
                        combined_price=(poly_yes_ask + kalshi_no_price) * 100,
                        arbitrage_percent=round(arb1, 2),
                        poly_yes_price=poly_yes_ask * 100,     # 使用实际买入价格
                        poly_no_price=poly_no_price * 100,
                        poly_action="买Yes",
                        kalshi_yes_price=kalshi_yes_price * 100,
                        kalshi_no_price=kalshi_no_price * 100,
                        kalshi_action="买No",
                        poly_market_id=poly_market.get('conditionId', poly_market.get('condition_id', '')),
                        kalshi_market_id=kalshi_market.get('market_id', ''),
                        match_confidence=round(match['confidence'], 2),
                        timestamp=time.time()
                    ))
                    self.opportunities_found += 1

                # 检查方向 2: 买 Kalshi Yes + 买 Poly No
                # 使用 poly_no_bid（实际买入价格，等于 1 - bestBid）
                arb2 = self.check_arbitrage(kalshi_yes_price, poly_no_bid, match['kalshi_title'])
                if arb2 is not None:
                    opportunities.append(CrossPlatformOpportunity(
                        arbitrage_type=CrossPlatformArbitrageType.KALSHI_YES_POLY_NO,
                        market_name=match['kalshi_title'][:60],
                        combined_price=(kalshi_yes_price + poly_no_bid) * 100,
                        arbitrage_percent=round(arb2, 2),
                        poly_yes_price=poly_yes_price * 100,
                        poly_no_price=poly_no_bid * 100,        # 使用实际买入价格
                        poly_action="买No",
                        kalshi_yes_price=kalshi_yes_price * 100,
                        kalshi_no_price=kalshi_no_price * 100,
                        kalshi_action="买Yes",
                        poly_market_id=poly_market.get('conditionId', poly_market.get('condition_id', '')),
                        kalshi_market_id=kalshi_market.get('market_id', ''),
                        match_confidence=round(match['confidence'], 2),
                        timestamp=time.time()
                    ))
                    self.opportunities_found += 1

            except Exception as e:
                self.logger.error(f"扫描匹配市场时出错: {type(e).__name__}: {e}")
                continue

        # 按套利空间排序
        opportunities.sort(key=lambda x: x.arbitrage_percent, reverse=True)

        self.logger.info(f"跨平台扫描完成: 发现 {len(opportunities)} 个套利机会")

        return opportunities

    def get_statistics(self) -> Dict:
        """获取统计信息"""
        return {
            'total_scans': self.total_scans,
            'opportunities_found': self.opportunities_found,
            'min_arbitrage_threshold': self.min_arbitrage_threshold
        }


def format_cross_platform_opportunity(opp: CrossPlatformOpportunity) -> str:
    """格式化跨平台套利机会为可读字符串"""
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
        f"   Kalshi:",
        f"     Yes: {opp.kalshi_yes_price:.2f}¢  No: {opp.kalshi_no_price:.2f}¢",
        f"     操作: {opp.kalshi_action}",
        f"",
        f"   匹配置信度: {opp.match_confidence:.2f}"
    ]

    return "\n".join(lines)


def create_cross_platform_monitor(config: Dict) -> CrossPlatformMonitor:
    """
    创建跨平台监控器

    Args:
        config: 配置字典

    Returns:
        CrossPlatformMonitor 实例
    """
    return CrossPlatformMonitor(config)
