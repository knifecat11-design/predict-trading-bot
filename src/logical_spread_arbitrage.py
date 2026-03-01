# -*- coding: utf-8 -*-
"""
Logical Spread Arbitrage Module - 逻辑价差套利检测器 (基于事件架构)

核心原理：
对于具有逻辑包含关系的两个事件 A（较难/子集）和 B（较易/超集）：
- 正常情况：P(A) < P(B)（较难的事件概率更低）
- 套利机会：当 P(A) ≥ P(B) 时（市场倒挂或定价异常）

架构设计（基于事件）：
1. 先获取多结果事件列表（每个事件包含多个子市场）
2. 对每个事件的子市场进行分析
3. 在同一事件内，检测子市场之间的逻辑关系
4. 检测价格倒挂（P(harder) ≥ P(easier)）

优势：
- 只在同一事件的子市场之间比较，避免跨事件错误匹配
- 例如 "Senate 2024" 和 "Trump 2025" 属于不同事件，不会被匹配
- 利用 Polymarket 的 /events 端点，天然保证子市场属于同一事件
"""

import re
import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Set
from datetime import datetime
from enum import Enum
from collections import Counter

logger = logging.getLogger(__name__)


# ============================================================
# 关键词库 - 用于识别市场类型和比较方向
# ============================================================

class ComparisonKeywords:
    """
    比较方向关键词库

    基于 Polymarket 实际数据分析（3000+ 子市场）
    """

    # ">=" 方向：表示达到或超过某个阈值
    # 频率：top(441), over(161), hit(15), above(5), break(4)
    GREATER_OR_EQUAL = {
        # 高频词
        'top',        # "BTC top $100k"
        'over',       # "over $300M FDV"
        'hit',        # "hit $1M"
        'above',      # "above $5,000"
        'exceed', 'exceeds', 'exceeding',
        'surpass', 'surpasses',
        'cross', 'crosses',
        'break', 'breaks',
        'reach', 'reaches',
        'tops', 'hits',
    }

    # "<=" 方向：表示低于或跌破某个阈值
    # 频率：drop(19), under(9)
    LESS_OR_EQUAL = {
        'under',      # "under $0.40"
        'below',      # "below $50k"
        'dip',        # "dip to $0.40"
        'drop', 'drops',
        'fall', 'falls',
        'decline', 'declines',
        'drop', 'drops',
    }

    # 符号形式
    SYMBOLS_GREATER = {'>', '≥', '+', '⬆️', '↑', '📈'}
    SYMBOLS_LESS = {'<', '≤', '-', '⬇️', '↓', '📉'}

    @classmethod
    def get_direction(cls, title: str) -> Optional[str]:
        """
        从标题中提取比较方向

        Returns: '>', '<', or None
        """
        title_lower = title.lower()

        # 先检查符号
        for char in title:
            if char in cls.SYMBOLS_GREATER:
                return '>'
            if char in cls.SYMBOLS_LESS:
                return '<'

        # 检查关键词
        for word in cls.GREATER_OR_EQUAL:
            if word in title_lower:
                return '>'

        for word in cls.LESS_OR_EQUAL:
            if word in title_lower:
                return '<'

        return None


class TimeKeywords:
    """
    时间相关关键词库

    用于识别时间窗口型套利机会
    """

    # 时间介词/连词
    PREPOSITIONS = {
        'by',         # "by December 31, 2025" - 截止日期
        'before',     # "before March 2026"
        'after',      # "after 2025"
        'until',      # "until 2026"
        'in',         # "in 2025"
        'during',     # "during 2025"
        'end',        # "end of 2025"
        'mid',        # "mid 2025"
        'early',      # "early 2025"
        'late',       # "late 2025"
        'start',      # "start of 2025"
        'q1', 'q2', 'q3', 'q4',  # 季度
        '1q', '2q', '3q', '4q',
    }

    # 月份
    MONTHS = {
        'january', 'february', 'march', 'april', 'may', 'june',
        'july', 'august', 'september', 'october', 'november', 'december',
        'jan', 'feb', 'mar', 'apr', 'may', 'jun',
        'jul', 'aug', 'sep', 'oct', 'nov', 'dec',
    }

    # 年份正则
    YEAR_PATTERN = r'\b(20[2-9][0-9])\b'

    @classmethod
    def extract_years(cls, title: str) -> Set[int]:
        """提取标题中的所有年份"""
        years = set()
        matches = re.findall(cls.YEAR_PATTERN, title)
        for m in matches:
            try:
                years.add(int(m))
            except ValueError:
                pass
        return years

    @classmethod
    def has_time_constraint(cls, title: str) -> bool:
        """判断标题是否包含时间约束"""
        title_lower = title.lower()
        return any(word in title_lower for word in cls.PREPOSITIONS) or bool(cls.extract_years(title))


class ValueKeywords:
    """
    数值类型关键词库

    用于识别不同类型的数值比较
    """

    # 价格相关
    PRICE_INDICATORS = {
        'price', 'pricing', 'priced',
        'trading at', 'trade at',
        'cost', 'value', 'valued',
    }

    # FDV 相关
    FDV_INDICATORS = {
        'fdv', 'fully diluted', 'fully-diluted', 'fully diluted valuation',
        'market cap', 'market-cap', 'marketcap',
        'valuation',
    }

    # 百分比/基点相关
    PERCENTAGE_INDICATORS = {
        '%', 'percent', 'percentage', 'pct',
        'bps', 'basis point', 'basis-points',
    }

    # 数量/范围相关
    QUANTITY_INDICATORS = {
        'people', 'person', 'individuals',
        'seats', 'states', 'votes',
        'count', 'number', 'amount',
        'deport', 'arrest', 'detain',
    }

    @classmethod
    def get_value_type(cls, title: str) -> str:
        """
        判断数值类型

        Returns: 'price', 'fdv', 'percentage', 'quantity', or 'unknown'
        """
        title_lower = title.lower()

        if any(ind in title_lower for ind in cls.FDV_INDICATORS):
            return 'fdv'
        if any(ind in title_lower for ind in cls.PERCENTAGE_INDICATORS):
            return 'percentage'
        if any(ind in title_lower for ind in cls.QUANTITY_INDICATORS):
            return 'quantity'
        if '$' in title or any(ind in title_lower for ind in cls.PRICE_INDICATORS):
            return 'price'

        return 'unknown'


class MarketType:
    """
    市场类型枚举

    基于实际 Polymarket 数据分析得出的模式
    """
    PRICE_THRESHOLD = "price_threshold"    # 价格阈值: BTC > $100k
    TIME_WINDOW = "time_window"            # 时间窗口: 2025 vs 2026
    DATE_DEADLINE = "date_deadline"        # 日期截止: by Dec 31 vs by Mar 31
    QUANTITY_RANGE = "quantity_range"      # 数量范围: 250k-500k vs 500k-750k
    PERCENTAGE_THRESHOLD = "percentage_threshold"  # 百分比: 50 bps vs 25 bps
    FDV_THRESHOLD = "fdv_threshold"        # FDV 阈值: FDV > $300M


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
    event_id: str = ""  # 所属事件 ID
    event_title: str = ""  # 所属事件标题
    hard_threshold: Optional[float] = None
    easy_threshold: Optional[float] = None
    comparison: str = ""  # ">", "<"
    value_type: str = ""  # price, fdv, percentage, quantity

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
class SubMarket:
    """子市场（事件内的单个市场）"""
    market_id: str
    title: str
    base_question: str  # 去掉数值/日期后的基础问题
    comparison: str  # ">", "<"
    threshold: Optional[float] = None
    year: Optional[int] = None
    date_str: Optional[str] = None  # 完整日期字符串，如 "December 31, 2025"
    yes_price: float = 0.0
    value_type: str = "unknown"  # price, fdv, percentage, quantity


class LogicalSpreadAnalyzer:
    """逻辑价差套利分析器（基于事件）"""

    # 价格提取模式 - 支持多种格式
    # $100k, $1.5M, $300,000, $3.00
    PRICE_PATTERN = r'\$([\d,]+(?:\.\d+)?)([kmbt]?)'

    # 百分比/基点提取
    PERCENTAGE_PATTERN = r'(\d+(?:\.\d+)?)(?:\+?)?\s*(?:%|percent|bps|basis\s*points?)'

    # 数量范围提取 (e.g., "250,000-500,000", "250k-500k")
    QUANTITY_RANGE_PATTERN = r'([\d,]+[kmb]?)(?:\s*[-–to]\s*([\d,]+[kmb]?))?'

    # 比较词（使用关键词库）
    COMPARISON_PATTERNS = {
        # ">" 方向
        'above': '>', 'over': '>', 'exceeds': '>', 'reach': '>', 'reaches': '>',
        'surpass': '>', 'surpasses': '>', 'tops': '>', 'hits': '>', 'top': '>',
        'cross': '>', 'crosses': '>', 'break': '>', 'breaks': '>',
        # "<" 方向
        'below': '<', 'under': '<', 'dip': '<', 'fall': '<', 'falls': '<',
        'drop': '<', 'drops': '<', 'decline': '<', 'declines': '<',
    }

    # 停止词
    STOP_WORDS = {
        'will', 'the', 'a', 'an', 'in', 'by', 'for', 'of', 'to', 'be',
        'or', 'and', 'with', 'from', 'at', 'on', 'before', 'after',
        'during', 'end', 'yes', 'no', 'any', 'all'
    }

    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.logger = logger

        # 配置参数
        lsa_config = self.config.get('logical_spread_arbitrage', {})
        self.min_threshold_diff_pct = lsa_config.get('min_threshold_diff_pct', 10.0)
        self.min_spread_threshold = lsa_config.get('min_spread_threshold', 0.0)
        self.fee_rate = lsa_config.get('fee_rate', 0.02)

    def extract_comparison(self, title: str) -> Optional[str]:
        """
        提取比较方向（使用关键词库）
        """
        # 优先使用关键词库
        direction = ComparisonKeywords.get_direction(title)
        if direction:
            return direction

        # 回退到正则模式匹配
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

    def extract_percentage(self, title: str) -> Optional[float]:
        """提取百分比/基点值"""
        title_lower = title.lower()

        # 检查是否是基点
        bps_match = re.search(r'(\d+(?:\+)?)\s*bps', title_lower)
        if bps_match:
            try:
                return float(bps_match.group(1).replace('+', ''))
            except ValueError:
                pass

        # 检查百分比
        pct_match = re.search(r'(\d+(?:\.\d+)?)\s*%', title)
        if pct_match:
            try:
                return float(pct_match.group(1))
            except ValueError:
                pass

        return None

    def extract_year(self, title: str) -> Optional[int]:
        """提取年份"""
        years = TimeKeywords.extract_years(title)
        return max(years) if years else None

    def extract_date_str(self, title: str) -> Optional[str]:
        """
        提取日期字符串，用于比较时间窗口

        例如：
        - "by December 31, 2025" → "December 31, 2025"
        - "in March 2026" → "March 2026"
        """
        # 提取月份和年份
        title_lower = title.lower()

        # 查找月份
        for month in TimeKeywords.MONTHS:
            if month in title_lower:
                # 尝试提取完整的日期短语
                # 匹配 "Month day, year" 或 "Month year"
                month_pattern = re.escape(month)
                date_match = re.search(
                    rf'{month_pattern}\s+(?:\d+,\s*)?(?:20[2-9][0-9])',
                    title,
                    re.IGNORECASE
                )
                if date_match:
                    return date_match.group(0)

                # 简单的月份+年份
                year_match = re.search(rf'{month_pattern}\s+(20[2-9][0-9])', title, re.IGNORECASE)
                if year_match:
                    return f"{month.capitalize()} {year_match.group(1)}"

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

    def get_value_type(self, title: str) -> str:
        """判断数值类型"""
        return ValueKeywords.get_value_type(title)

    def parse_submarket(self, market: Dict) -> Optional[SubMarket]:
        """
        解析子市场为结构化数据

        Args:
            market: Polymarket 市场字典，包含 conditionId, question, bestAsk 等
        """
        title = market.get('question', market.get('title', ''))
        if not title:
            return None

        comparison = self.extract_comparison(title)
        if not comparison:
            return None

        # 获取价格
        yes_price = 0.0
        if market.get('bestAsk') is not None:
            yes_price = float(market.get('bestAsk', 0))
        elif market.get('outcomePrices'):
            try:
                outcome_prices = json.loads(market.get('outcomePrices', '[]'))
                if outcome_prices:
                    yes_price = float(outcome_prices[0])
            except (json.JSONDecodeError, ValueError, IndexError):
                pass
        elif market.get('price') is not None:
            yes_price = float(market.get('price', 0))

        # 提取阈值（可能是价格、百分比或数量）
        threshold = self.extract_threshold(title)
        if threshold is None:
            threshold = self.extract_percentage(title)

        return SubMarket(
            market_id=market.get('conditionId', market.get('id', '')),
            title=title,
            base_question=self.get_base_question(title),
            comparison=comparison,
            threshold=threshold,
            year=self.extract_year(title),
            date_str=self.extract_date_str(title),
            yes_price=yes_price,
            value_type=self.get_value_type(title)
        )

    def find_price_threshold_pairs_in_event(
        self,
        submarkets: List[SubMarket],
        event_id: str,
        event_title: str
    ) -> List[EventPair]:
        """
        在同一事件内查找价格阈值型套利机会

        条件：
        1. 方向一致（都是 > 或都是 <）
        2. Hard 阈值 > Easy 阈值（对于 > 方向）
        3. 阈值差异足够大（至少 min_threshold_diff_pct）

        支持类型：
        - 价格阈值: BTC > $100k vs BTC > $50k
        - FDV 阈值: FDV > $300M vs FDV > $100M
        - 百分比阈值: 50+ bps vs 25 bps

        Args:
            submarkets: 同一事件的子市场列表
            event_id: 事件 ID
            event_title: 事件标题
        """
        pairs = []

        # 只保留有阈值的子市场
        with_threshold = [s for s in submarkets if s.threshold is not None]

        # 按阈值排序
        with_threshold.sort(key=lambda s: s.threshold or 0)

        # 两两比较
        for i in range(len(with_threshold)):
            for j in range(i + 1, len(with_threshold)):
                s1 = with_threshold[i]
                s2 = with_threshold[j]

                # 方向必须一致
                if s1.comparison != s2.comparison:
                    continue

                # 数值类型应该相同（price vs price, percentage vs percentage）
                if s1.value_type != s2.value_type and s1.value_type != 'unknown' and s2.value_type != 'unknown':
                    continue

                # 确定哪个是 hard/easy
                if s1.comparison == '>':
                    # 对于 ">" 方向：阈值大的更难
                    if s1.threshold < s2.threshold:
                        hard, easy = s2, s1
                    else:
                        hard, easy = s1, s2
                else:  # "<" 方向
                    # 对于 "<" 方向：阈值小的更难
                    if s1.threshold < s2.threshold:
                        hard, easy = s1, s2
                    else:
                        hard, easy = s2, s1

                # 计算阈值差异百分比
                if easy.threshold > 0:
                    diff_pct = abs(hard.threshold - easy.threshold) / easy.threshold * 100
                    if diff_pct < self.min_threshold_diff_pct:
                        continue

                # 创建事件对
                value_type_name = {
                    'fdv': 'FDV',
                    'percentage': '百分比',
                    'quantity': '数量',
                    'price': '价格',
                }.get(hard.value_type, '阈值')

                pair = EventPair(
                    hard_market_id=hard.market_id,
                    hard_title=hard.title,
                    easy_market_id=easy.market_id,
                    easy_title=easy.title,
                    logical_type=LogicalType.PRICE_THRESHOLD,
                    relationship_desc=f"{value_type_name} ({hard.comparison}): {self._format_threshold(hard.threshold)} vs {self._format_threshold(easy.threshold)}",
                    platform="polymarket",
                    hard_threshold=hard.threshold,
                    easy_threshold=easy.threshold,
                    comparison=hard.comparison,
                    event_id=event_id,
                    event_title=event_title,
                    hard_price=hard.yes_price,
                    easy_price=easy.yes_price,
                    value_type=hard.value_type
                )

                pair.calculate_spread()
                pairs.append(pair)

        return pairs

    def find_time_window_pairs_in_event(
        self,
        submarkets: List[SubMarket],
        event_id: str,
        event_title: str
    ) -> List[EventPair]:
        """
        在同一事件内查找时间窗口型套利机会

        条件：
        1. 标题高度相似（去掉年份后）
        2. 有明确的时间差异（年份或日期）
        3. 早期时间是 hard，晚期时间是 easy

        支持类型：
        - 年份窗口: 2025 vs 2026
        - 日期窗口: by Dec 31 vs by Mar 31

        Args:
            submarkets: 同一事件的子市场列表
            event_id: 事件 ID
            event_title: 事件标题
        """
        pairs = []

        # 按是否有时间/日期分组
        with_year = [s for s in submarkets if s.year is not None]
        with_date = [s for s in submarkets if s.date_str is not None]

        # 年份型比较
        with_year.sort(key=lambda s: s.year or 0)
        for i in range(len(with_year)):
            for j in range(i + 1, len(with_year)):
                s1 = with_year[i]
                s2 = with_year[j]

                # 检查年份差（只匹配相邻或相近年份）
                year_diff = (s2.year or 0) - (s1.year or 0)
                if year_diff > 2 or year_diff < 1:
                    continue

                # 检查基础问题相似度
                if not self._are_titles_similar(s1, s2):
                    continue

                # 早期是 hard，晚期是 easy
                hard, easy = s1, s2

                pair = EventPair(
                    hard_market_id=hard.market_id,
                    hard_title=hard.title,
                    easy_market_id=easy.market_id,
                    easy_title=easy.title,
                    logical_type=LogicalType.TIME_WINDOW,
                    relationship_desc=f"时间窗口: {hard.year} vs {easy.year}",
                    platform="polymarket",
                    hard_threshold=float(hard.year),
                    easy_threshold=float(easy.year),
                    comparison="earlier",
                    event_id=event_id,
                    event_title=event_title,
                    hard_price=hard.yes_price,
                    easy_price=easy.yes_price,
                    value_type="time"
                )

                pair.calculate_spread()
                pairs.append(pair)

        # 日期型比较（如 "by Dec 31" vs "by Mar 31"）
        # 注意：需要更高的相似度要求，避免匹配完全不同的事件
        if len(with_date) >= 2:
            # 按月份排序（简单处理）
            month_order = {
                'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
                'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12
            }

            def get_month_key(s: SubMarket) -> int:
                if s.date_str:
                    for month, num in month_order.items():
                        if month in s.date_str.lower():
                            return num
                return 999

            with_date.sort(key=get_month_key)

            for i in range(len(with_date)):
                for j in range(i + 1, len(with_date)):
                    s1 = with_date[i]
                    s2 = with_date[j]

                    # 关键：比较方向必须一致（不能一个 reach 一个 dip）
                    if s1.comparison != s2.comparison:
                        continue

                    # 检查基础问题相似度（日期型需要更高相似度）
                    if not self._are_titles_similar(s1, s2, min_similarity=0.75):
                        continue

                    # 早期是 hard，晚期是 easy
                    hard, easy = s1, s2

                    pair = EventPair(
                        hard_market_id=hard.market_id,
                        hard_title=hard.title,
                        easy_market_id=easy.market_id,
                        easy_title=easy.title,
                        logical_type=LogicalType.TIME_WINDOW,
                        relationship_desc=f"时间窗口: {hard.date_str} vs {easy.date_str}",
                        platform="polymarket",
                        hard_threshold=0.0,  # 日期无法用数值表示
                        easy_threshold=0.0,
                        comparison="earlier",
                        event_id=event_id,
                        event_title=event_title,
                        hard_price=hard.yes_price,
                        easy_price=easy.yes_price,
                        value_type="time"
                    )

                    pair.calculate_spread()
                    pairs.append(pair)

        return pairs

    def _are_titles_similar(self, s1: SubMarket, s2: SubMarket, min_similarity: float = 0.6) -> bool:
        """判断两个子市场的基础问题是否相似"""
        words1 = set(s1.base_question.split())
        words2 = set(s2.base_question.split())

        if not words1 or not words2:
            return False

        # 计算交集比例
        intersection = words1 & words2
        union = words1 | words2
        similarity = len(intersection) / len(union) if union else 0

        return similarity >= min_similarity

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
        elif value >= 0.01:
            return f"{value:.2f}%"
        else:
            return f"{value}"


class LogicalSpreadArbitrageDetector:
    """逻辑价差套利检测器（主类）"""

    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.analyzer = LogicalSpreadAnalyzer(config)
        self.logger = logger

        lsa_config = self.config.get('logical_spread_arbitrage', {})
        self.min_spread_threshold = lsa_config.get('min_spread_threshold', 0.0)
        self.fee_rate = lsa_config.get('fee_rate', 0.02)

        self._cached_pairs: List[EventPair] = []

    def scan_events(
        self,
        events: List[Dict],
        platform: str = "polymarket"
    ) -> List[EventPair]:
        """
        扫描事件列表，检测逻辑价差套利机会

        Args:
            events: 从 /events API 获取的事件列表
                   每个事件包含 id, title, markets[] 等字段
            platform: 平台名称

        Returns:
            检测到的套利机会列表
        """
        all_pairs = []

        for event in events:
            event_id = event.get('id', '')
            event_title = event.get('title', event.get('slug', ''))
            markets = event.get('markets', [])

            if not markets or len(markets) < 2:
                continue  # 至少需要 2 个子市场才能形成对

            # 解析子市场
            submarkets = []
            for market in markets:
                submarket = self.analyzer.parse_submarket(market)
                if submarket:
                    submarkets.append(submarket)

            if len(submarkets) < 2:
                continue

            # 查找价格阈值型套利
            price_pairs = self.analyzer.find_price_threshold_pairs_in_event(
                submarkets, event_id, event_title
            )
            all_pairs.extend(price_pairs)

            # 查找时间窗口型套利
            time_pairs = self.analyzer.find_time_window_pairs_in_event(
                submarkets, event_id, event_title
            )
            all_pairs.extend(time_pairs)

        # 过滤：只保留有套利机会的（spread >= 0）
        arbitrage_pairs = [p for p in all_pairs if p.has_arbitrage]

        # 进一步过滤：价差阈值
        if self.min_spread_threshold > 0:
            arbitrage_pairs = [
                p for p in arbitrage_pairs
                if p.spread * 100 >= self.min_spread_threshold
            ]

        self._cached_pairs = arbitrage_pairs
        self.logger.info(f"[LogicalSpread] 扫描 {len(events)} 个事件，检测到 {len(arbitrage_pairs)} 个套利机会")

        return arbitrage_pairs

    def update_prices(
        self,
        price_dict: Dict[str, float]
    ) -> List[EventPair]:
        """
        更新事件对价格并重新检测套利机会

        Args:
            price_dict: {market_id: yes_price} 字典

        Returns:
            有套利机会的事件对列表
        """
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

        event_info = f"\n**事件:** {pair.event_title[:50]}..." if pair.event_title else ""

        return (
            f"**🔗 逻辑价差套利**\n"
            f"\n"
            f"**类型:** {self._get_type_name(pair.logical_type)}\n"
            f"**平台:** {pair.platform.title()}\n"
            f"{event_info}"
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
