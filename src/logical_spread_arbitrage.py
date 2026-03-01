# -*- coding: utf-8 -*-
"""
Logical Spread Arbitrage Module - 逻辑价差套利检测器 (严格版本)

核心原理：
对于具有逻辑包含关系的两个事件 A（较难/子集）和 B（较易/超集）：
- 正常情况：P(A) < P(B)（较难的事件概率更低）
- 套利机会：当 P(A) ≥ P(B) 时（市场倒挂或定价异常）

严格匹配条件（防止错误匹配）：
1. 同一市场：两个事件必须属于同一个"事件族"
   - 标题的核心部分必须高度相似（去掉数值/日期后的部分）
   - 例如："BTC > $50k in 2025" 和 "BTC > $100k in 2025" 是同一事件族
   - "Trump win 2024" 和 "Trump win 2028" 是同一事件族

2. 方向一致性：条件方向必须相同
   - 都是 ">" 或都是 "<"
   - 不能一个"reach $3"另一个"dip to $0.4"

3. 包含关系：Hard 的条件必须严格包含在 Easy 中
   - 对于 ">" 方向：Hard 阈值 > Easy 阈值
   - 对于 "<" 方向：Hard 阈值 < Easy 阈值
"""

import re
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from enum import Enum
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


class LogicalType(Enum):
    """逻辑关系类型"""
    PRICE_THRESHOLD = "price_threshold"  # 价格阈值包含
    TIME_WINDOW = "time_window"          # 时间窗口包含


@dataclass
class EventPair:
    """逻辑事件对"""
    hard_market_id: str
    hard_title: str
    easy_market_id: str
    easy_title: str

    hard_price: float = 0.0
    easy_price: float = 0.0

    logical_type: LogicalType = LogicalType.PRICE_THRESHOLD
    relationship_desc: str = ""

    spread: float = 0.0
    arbitrage_cost: float = 0.0
    arbitrage_profit: float = 0.0
    has_arbitrage: bool = False

    platform: str = "polymarket"
    detected_at: str = ""
    hard_threshold: Optional[float] = None
    easy_threshold: Optional[float] = None
    comparison: str = ""  # ">", "<"

    def __post_init__(self):
        if not self.detected_at:
            self.detected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @property
    def pair_key(self) -> str:
        return f"{self.logical_type.value}:{self.hard_market_id}:{self.easy_market_id}"

    def calculate_spread(self) -> None:
        self.spread = self.hard_price - self.easy_price
        if self.spread >= 0:
            self.has_arbitrage = True
            self.arbitrage_cost = (1 - self.hard_price) + self.easy_price
            self.arbitrage_profit = 1 - self.arbitrage_cost
        else:
            self.has_arbitrage = False
            self.arbitrage_cost = 0
            self.arbitrage_profit = 0


@dataclass
class MarketPattern:
    """市场模式（提取的结构化信息）"""
    market_id: str
    title: str

    # 提取的模式
    base_question: str       # 去掉数值/日期后的基础问题
    comparison: str          # ">", "<", ">=", "<="
    threshold: Optional[float] = None
    year: Optional[int] = None

    # 原始数据
    yes_price: float = 0.0


class EventPairExtractor:
    """事件对提取器（严格版本）"""

    # 价格提取模式（更严格）
    PRICE_PATTERN = r'\$([\d,]+(?:\.\d+)?)([kmbt]?)'

    # 年份提取
    YEAR_PATTERN = r'\b(20[2-9][0-9])\b'

    # 比较词（方向性）
    COMPARISON_PATTERNS = {
        # ">" 方向
        'above': '>',
        'over': '>',
        'exceeds': '>',
        'reach': '>',
        'reaches': '>',
        'surpass': '>',
        'surpasses': '>',
        'tops': '>',
        'hits': '>',
        'cross': '>',
        'crosses': '>',
        'break': '>',
        'breaks': '>',
        # "<" 方向
        'below': '<',
        'under': '<',
        'dip': '<',
        'fall': '<',
        'falls': '<',
        'drop': '<',
        'drops': '<',
        'decline': '<',
        'declines': '<',
    }

    # 停止词（用于提取基础问题）
    STOP_WORDS = {
        'will', 'the', 'a', 'an', 'in', 'by', 'for', 'of', 'to', 'be',
        'or', 'and', 'with', 'from', 'at', 'on', 'before', 'after',
        'during', 'end', 'yes', 'no'
    }

    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.logger = logger

        # 最小相似度阈值（用于判断是否同一市场）
        self.min_base_similarity = 0.75

    def extract_comparison(self, title: str) -> Optional[str]:
        """提取比较方向"""
        title_lower = title.lower()

        for word, direction in self.COMPARISON_PATTERNS.items():
            if word in title_lower:
                return direction

        # 默认：如果有数字阈值，假设是 ">" 方向
        if self._extract_price_value(title) is not None:
            return '>'

        return None

    def _extract_price_value(self, title: str) -> Optional[float]:
        """提取价格的数值部分（不含后缀）"""
        match = re.search(self.PRICE_PATTERN, title, re.IGNORECASE)
        if match:
            try:
                price_str = match.group(1).replace(',', '')
                price = float(price_str)
                return price
            except ValueError:
                pass
        return None

    def extract_threshold(self, title: str) -> Optional[float]:
        """提取完整阈值（处理后缀）"""
        match = re.search(self.PRICE_PATTERN, title, re.IGNORECASE)
        if match:
            try:
                price_str = match.group(1).replace(',', '')
                price = float(price_str)
                suffix = match.group(2).lower()

                multipliers = {'k': 1000, 'm': 1000000, 'b': 1000000000, 't': 1000000000000}
                if suffix in multipliers:
                    price *= multipliers[suffix]

                return price
            except ValueError:
                pass
        return None

    def extract_year(self, title: str) -> Optional[int]:
        """提取年份"""
        matches = re.findall(self.YEAR_PATTERN, title)
        if matches:
            try:
                return int(matches[0])
            except ValueError:
                pass
        return None

    def get_base_question(self, title: str) -> str:
        """
        提取基础问题（去掉数值、日期、比较词）

        例如：
        - "Will BTC reach $100k in 2025?" → "will btc reach in"
        - "XRP above $3 by Dec 2026?" → "xrp above by"
        """
        # 移除数字和价格
        text = re.sub(r'\$[\d,]+(?:\.\d+)?[kmbt]?', '[NUM]', title, flags=re.IGNORECASE)
        text = re.sub(r'\b\d+\b', '[NUM]', text)

        # 移除年份
        text = re.sub(r'\b20[2-9][0-9]\b', '[YEAR]', text)

        # 移除比较词
        for word in self.COMPARISON_PATTERNS.keys():
            text = re.sub(r'\b' + word + r'\b', '', text, flags=re.IGNORECASE)

        # 移除停用词
        words = text.lower().split()
        words = [w for w in words if w not in self.STOP_WORDS and len(w) > 1]

        # 去重并排序
        words = sorted(set(words))

        return ' '.join(words)

    def parse_market(self, market: Dict) -> Optional[MarketPattern]:
        """解析市场为结构化模式"""
        title = market.get('title', market.get('question', ''))
        if not title:
            return None

        comparison = self.extract_comparison(title)
        if not comparison:
            return None

        return MarketPattern(
            market_id=market.get('id', market.get('conditionId', '')),
            title=title,
            base_question=self.get_base_question(title),
            comparison=comparison,
            threshold=self.extract_threshold(title),
            year=self.extract_year(title),
            yes_price=market.get('yes', 0)
        )

    def are_same_market_family(self, pattern1: MarketPattern, pattern2: MarketPattern) -> bool:
        """
        判断两个市场是否属于同一个事件族

        条件：
        1. 基础问题相似度 >= 阈值
        2. 比较方向相同
        """
        # 方向必须一致
        if pattern1.comparison != pattern2.comparison:
            return False

        # 基础问题相似度
        similarity = SequenceMatcher(
            None,
            pattern1.base_question,
            pattern2.base_question
        ).ratio()

        return similarity >= self.min_base_similarity

    def find_price_threshold_pairs(
        self,
        markets: List[Dict],
        min_threshold_diff_pct: float = 10.0
    ) -> List[EventPair]:
        """
        查找价格阈值型事件对（严格版本）

        条件：
        1. 同一事件族（基础问题相似）
        2. 方向一致（都是 > 或都是 <）
        3. Hard 阈值 > Easy 阈值（对于 > 方向）
        """
        pairs = []

        # 解析所有市场
        patterns = []
        for market in markets:
            pattern = self.parse_market(market)
            if pattern and pattern.threshold is not None:
                patterns.append(pattern)

        # 按阈值排序
        patterns.sort(key=lambda p: p.threshold or 0)

        # 两两比较
        for i in range(len(patterns)):
            for j in range(i + 1, len(patterns)):
                p1 = patterns[i]
                p2 = patterns[j]

                # 检查是否同一事件族
                if not self.are_same_market_family(p1, p2):
                    continue

                # 确定哪个是 hard/easy
                if p1.comparison == '>':
                    # 对于 ">" 方向：阈值大的更难
                    if p1.threshold < p2.threshold:
                        hard, easy = p2, p1
                    else:
                        hard, easy = p1, p2
                else:  # "<" 方向
                    # 对于 "<" 方向：阈值小的更难
                    if p1.threshold < p2.threshold:
                        hard, easy = p1, p2
                    else:
                        hard, easy = p2, p1

                # 计算阈值差异
                diff_pct = abs(hard.threshold - easy.threshold) / easy.threshold * 100
                if diff_pct < min_threshold_diff_pct:
                    continue

                pairs.append(EventPair(
                    hard_market_id=hard.market_id,
                    hard_title=hard.title,
                    easy_market_id=easy.market_id,
                    easy_title=easy.title,
                    logical_type=LogicalType.PRICE_THRESHOLD,
                    relationship_desc=f"价格阈值 ({hard.comparison}): {self._format_threshold(hard.threshold)} vs {self._format_threshold(easy.threshold)}",
                    platform="polymarket",
                    hard_threshold=hard.threshold,
                    easy_threshold=easy.threshold,
                    comparison=hard.comparison
                ))

        self.logger.info(f"[LSA] 价格阈值对: {len(pairs)} 个")
        return pairs

    def find_time_window_pairs(
        self,
        markets: List[Dict]
    ) -> List[EventPair]:
        """
        查找时间窗口型事件对（严格版本）

        条件：
        1. 同一事件族（基础问题相似）
        2. 仅年份不同
        3. 早期年份是 hard，晚期年份是 easy
        """
        pairs = []

        # 解析所有市场
        patterns = []
        for market in markets:
            pattern = self.parse_market(market)
            if pattern and pattern.year is not None:
                patterns.append(pattern)

        # 按年份排序
        patterns.sort(key=lambda p: p.year or 0)

        # 两两比较
        for i in range(len(patterns)):
            for j in range(i + 1, len(patterns)):
                p1 = patterns[i]
                p2 = patterns[j]

                # 检查是否同一事件族
                if not self.are_same_market_family(p1, p2):
                    continue

                # 检查年份差（只匹配相邻或相近年份）
                year_diff = (p2.year or 0) - (p1.year or 0)
                if year_diff > 2 or year_diff < 1:
                    continue

                # 早期是 hard，晚期是 easy
                hard, easy = p1, p2

                pairs.append(EventPair(
                    hard_market_id=hard.market_id,
                    hard_title=hard.title,
                    easy_market_id=easy.market_id,
                    easy_title=easy.title,
                    logical_type=LogicalType.TIME_WINDOW,
                    relationship_desc=f"时间窗口: {hard.year} vs {easy.year}",
                    platform="polymarket",
                    hard_threshold=float(hard.year),
                    easy_threshold=float(easy.year),
                    comparison="earlier"
                ))

        self.logger.info(f"[LSA] 时间窗口对: {len(pairs)} 个")
        return pairs

    @staticmethod
    def _format_threshold(value: float) -> str:
        """格式化阈值显示"""
        if value >= 1_000_000_000:
            return f"${value/1_000_000_000:.1f}B"
        elif value >= 1_000_000:
            return f"${value/1_000_000:.1f}M"
        elif value >= 1_000:
            return f"${value/1_000:.1f}K"
        elif value >= 1:
            return f"${value:.2f}"
        return str(value)


class LogicalSpreadArbitrageDetector:
    """逻辑价差套利检测器"""

    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.extractor = EventPairExtractor(config)
        self.logger = logger

        lsa_config = self.config.get('logical_spread_arbitrage', {})
        self.min_spread_threshold = lsa_config.get('min_spread_threshold', 0.5)
        self.fee_rate = lsa_config.get('fee_rate', 0.02)
        self.min_threshold_diff_pct = lsa_config.get('min_threshold_diff_pct', 10.0)

        self._cached_pairs: List[EventPair] = []

    def detect_pairs(
        self,
        markets: List[Dict],
        platform: str = "polymarket"
    ) -> List[EventPair]:
        """检测市场中的逻辑事件对"""
        pairs = []

        # 查找价格阈值型事件对
        price_pairs = self.extractor.find_price_threshold_pairs(
            markets,
            min_threshold_diff_pct=self.min_threshold_diff_pct
        )
        pairs.extend(price_pairs)

        # 查找时间窗口型事件对
        time_pairs = self.extractor.find_time_window_pairs(markets)
        pairs.extend(time_pairs)

        self._cached_pairs = pairs
        self.logger.info(f"[LogicalSpread] 检测到 {len(pairs)} 个事件对（严格模式）")

        return pairs

    def update_prices(
        self,
        price_dict: Dict[str, float]
    ) -> List[EventPair]:
        """更新事件对价格并检测套利机会"""
        arbitrage_pairs = []

        for pair in self._cached_pairs:
            hard_price = price_dict.get(pair.hard_market_id)
            easy_price = price_dict.get(pair.easy_market_id)

            if hard_price is None or easy_price is None:
                continue

            pair.hard_price = hard_price
            pair.easy_price = easy_price
            pair.calculate_spread()

            if pair.has_arbitrage:
                spread_pct = pair.spread * 100
                if spread_pct >= self.min_spread_threshold:
                    arbitrage_pairs.append(pair)

        return arbitrage_pairs

    def scan_markets(
        self,
        markets: List[Dict],
        price_dict: Dict[str, float],
        platform: str = "polymarket"
    ) -> List[EventPair]:
        """完整扫描"""
        self.detect_pairs(markets, platform)
        return self.update_prices(price_dict)

    def format_arbitrage_message(self, pair: EventPair) -> str:
        """格式化套利通知消息"""
        spread_pct = pair.spread * 100
        profit_pct = pair.arbitrage_profit * 100
        cost_pct = pair.arbitrage_cost * 100
        hard_yes_pct = pair.hard_price * 100
        easy_yes_pct = pair.easy_price * 100

        if spread_pct > 0:
            status_text = f"市场倒挂 (+{spread_pct:.2f}%)"
        else:
            status_text = f"定价异常 (价差为0)"

        return (
            f"**🔗 逻辑价差套利**\n"
            f"\n"
            f"**类型:** {self._get_type_name(pair.logical_type)}\n"
            f"**平台:** {pair.platform.title()}\n"
            f"\n"
            f"**逻辑关系:** {pair.relationship_desc}\n"
            f"\n"
            f"**较难事件 (Hard):**\n"
            f"  {pair.hard_title[:60]}...\n"
            f"  YES价格: {hard_yes_pct:.1f}%\n"
            f"\n"
            f"**较易事件 (Easy):**\n"
            f"  {pair.easy_title[:60]}...\n"
            f"  YES价格: {easy_yes_pct:.1f}%\n"
            f"\n"
            f"**状态:** {status_text} (正常应为负)\n"
            f"**套利成本:** {cost_pct:.1f}%\n"
            f"**预期收益:** {profit_pct:+.2f}%\n"
            f"\n"
            f"**策略:** 买入 Hard 的 NO + 买入 Easy 的 YES\n"
            f"\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )

    @staticmethod
    def _get_type_name(logical_type: LogicalType) -> str:
        names = {
            LogicalType.PRICE_THRESHOLD: "价格阈值",
            LogicalType.TIME_WINDOW: "时间窗口",
        }
        return names.get(logical_type, "未知类型")


def create_logical_spread_detector(config: Dict) -> LogicalSpreadArbitrageDetector:
    return LogicalSpreadArbitrageDetector(config)
