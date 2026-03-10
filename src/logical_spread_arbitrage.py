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
    }

    # 符号形式（不含 +/- 因为它们经常出现在非比较上下文中，如 "10-year", "100+"）
    SYMBOLS_GREATER = {'>', '≥', '⬆️', '↑', '📈'}
    SYMBOLS_LESS = {'<', '≤', '⬇️', '↓', '📉'}

    @classmethod
    def get_direction(cls, title: str) -> Optional[str]:
        """
        从标题中提取比较方向

        Returns: '>', '<', or None
        """
        title_lower = title.lower()

        # 最高优先: 显式方向标记 (HIGH)/(LOW)
        # Polymarket 指数类市场使用 "(HIGH)"/"(LOW)" 明确标注方向
        if '(high)' in title_lower:
            return '>'
        if '(low)' in title_lower:
            return '<'

        # 高优先: "X or lower/higher" 短语修饰符覆盖关键词方向
        # "reach 3.5% or lower" → <，即使 "reach" 是 > 关键词
        if re.search(r'or\s+(?:lower|less|below|fewer)', title_lower):
            return '<'
        if re.search(r'or\s+(?:higher|more|above|greater)', title_lower):
            return '>'

        # 检查符号
        for char in title:
            if char in cls.SYMBOLS_GREATER:
                return '>'
            if char in cls.SYMBOLS_LESS:
                return '<'

        # 使用单词边界匹配关键词（避免 "drop" 匹配 "airdrop", "top" 匹配 "stop" 等）
        words_in_title = set(re.findall(r'\b[a-z]+\b', title_lower))

        for word in cls.GREATER_OR_EQUAL:
            if word in words_in_title:
                return '>'

        for word in cls.LESS_OR_EQUAL:
            if word in words_in_title:
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
    """逻辑事件对 — 基于确定方向的结构套利

    核心流程:
    1. 确认逻辑包含关系: Hard⊂Easy (Hard 是 Easy 的子集)
       - 价格阈值: "BTC > $100k"(Hard) ⊂ "BTC > $50k"(Easy)
       - 时间窗口: "by March 15"(Hard) ⊂ "by March 31"(Easy)
    2. 检测价格倒挂: P(Hard_YES) ≥ P(Easy_YES)
    3. 计算套利成本: 买 Hard_NO(ask) + 买 Easy_YES(ask)
    4. 若成本 < 1.0 → 结构性套利（保证 payout ≥ 1）

    为什么只有一个方向:
    - Hard⊂Easy 意味着 Hard=Yes → Easy=Yes（Hard 发生则 Easy 必发生）
    - 因此 Hard=No ∨ Easy=Yes 覆盖所有可能结果
    - 买 Hard_NO + 买 Easy_YES 保证最低收益 = 1
    - 反向组合（买 Hard_YES + 买 Easy_NO）在 "Hard=No, Easy=Yes" 时收益 = 0，不是结构套利
    """
    # Hard = 子集/更难的市场 (A), Easy = 超集/更容易的市场 (B)
    hard_market_id: str
    hard_title: str
    easy_market_id: str
    easy_title: str

    hard_price: float = 0.0           # Hard YES mid-price
    easy_price: float = 0.0           # Easy YES mid-price

    logical_type: LogicalType = LogicalType.PRICE_THRESHOLD
    relationship_desc: str = ""

    # === 套利检测 ===
    spread: float = 0.0               # hard_mid - easy_mid（≥0 = 价格倒挂）
    has_arbitrage: bool = False        # 存在价格倒挂（mid-price 级别）

    # === 套利执行（买 Hard_NO + 买 Easy_YES）===
    arb_cost: float = 0.0             # Hard_NO_ask + Easy_YES_ask
    arb_profit: float = 0.0           # 1 - arb_cost
    arb_direction: str = ""           # 执行方向描述

    # 兼容字段（dashboard 使用）
    arbitrage_cost: float = 0.0
    arbitrage_profit: float = 0.0
    ask_cost: float = 0.0
    ask_profit: float = 0.0

    # 信号分层
    # "executable"      — ask 成本 < 1 + 两腿流动性充足
    # "limit_candidate" — mid-price 倒挂但 ask 成本 ≥ 1，适合挂限价单
    # "monitor_only"    — 无倒挂
    signal_tier: str = "monitor_only"

    platform: str = "polymarket"
    detected_at: str = ""
    event_id: str = ""
    event_title: str = ""
    event_slug: str = ""
    hard_threshold: Optional[float] = None
    easy_threshold: Optional[float] = None
    comparison: str = ""
    value_type: str = ""

    # YES 代币盘口数据
    hard_best_bid: Optional[float] = None
    hard_best_ask: Optional[float] = None
    hard_mid: Optional[float] = None
    hard_spread: Optional[float] = None
    easy_best_bid: Optional[float] = None
    easy_best_ask: Optional[float] = None
    easy_mid: Optional[float] = None
    easy_spread: Optional[float] = None
    hard_has_liquidity: bool = True
    easy_has_liquidity: bool = True

    # NO 代币盘口数据（推导）
    hard_no_bid: Optional[float] = None   # 1 - hard_yes_ask
    hard_no_ask: Optional[float] = None   # 1 - hard_yes_bid
    easy_no_bid: Optional[float] = None   # 1 - easy_yes_ask
    easy_no_ask: Optional[float] = None   # 1 - easy_yes_bid

    # 交易量
    hard_volume: float = 0.0
    easy_volume: float = 0.0

    # 流动性过滤阈值
    max_spread_rate: float = 0.10     # 最大允许价差率（默认 10%）

    def __post_init__(self):
        if not self.detected_at:
            self.detected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @property
    def pair_key(self) -> str:
        return f"{self.logical_type.value}:{self.hard_market_id}:{self.easy_market_id}"

    def calculate_spread(self) -> None:
        """基于确定方向的单向套利计算

        流程:
        1. 检测 mid-price 倒挂: P(Hard_YES) ≥ P(Easy_YES)
        2. 计算唯一正确的组合: 买 Hard_NO(ask) + 买 Easy_YES(ask)
        3. 判断 ask 成本 < 1.0 → 可执行套利
        4. 分层: executable / limit_candidate / monitor_only
        """
        # === Step 1: Mid-price 倒挂检测 ===
        self.spread = self.hard_price - self.easy_price
        self.has_arbitrage = (self.spread >= 0)

        if not self.has_arbitrage:
            self.signal_tier = "monitor_only"
            return

        # === Step 2: 唯一正确的组合成本 ===
        # Hard⊂Easy → 买 Hard_NO + 买 Easy_YES（保证 payout ≥ 1）
        hard_no_ask = self.hard_no_ask      # = 1 - Hard_YES_bid
        easy_yes_ask = self.easy_best_ask

        if (hard_no_ask is not None and easy_yes_ask is not None
                and hard_no_ask > 0 and easy_yes_ask > 0):
            self.arb_cost = hard_no_ask + easy_yes_ask
            self.arb_profit = 1.0 - self.arb_cost
            self.arb_direction = (
                f"买Hard_NO({hard_no_ask*100:.1f}¢) + "
                f"买Easy_YES({easy_yes_ask*100:.1f}¢)"
            )
        else:
            # 无完整 ask 数据，用 mid-price 估算
            self.arb_cost = (1.0 - self.hard_price) + self.easy_price
            self.arb_profit = 1.0 - self.arb_cost
            self.arb_direction = "mid-price 估算"

        # 同步兼容字段
        self.arbitrage_cost = self.arb_cost
        self.arbitrage_profit = self.arb_profit
        self.ask_cost = self.arb_cost
        self.ask_profit = self.arb_profit

        # === Step 3: 信号分层 ===
        self.signal_tier = self._classify_signal_tier()

    def _spread_rate(self, bid: Optional[float], ask: Optional[float]) -> Optional[float]:
        """计算价差率: (ask - bid) / bid"""
        if bid is not None and ask is not None and bid > 0:
            return (ask - bid) / bid
        return None

    @staticmethod
    def _pct(val) -> str:
        """格式化为 cents 显示"""
        if val is None:
            return "N/A"
        return f"{val * 100:.1f}¢"

    def _classify_signal_tier(self) -> str:
        """信号分层

        Tier 1 - executable:
            - ask 成本 < 1.0（真实可执行利润 > 0）
            - 两腿价差率 ≤ 10%（流动性充足）

        Tier 2 - limit_candidate:
            - mid-price 有倒挂（has_arbitrage=True）
            - 但 ask 成本 ≥ 1.0（适合挂限价单等机会）
            - 或 ask 成本 < 1.0 但流动性不足

        Tier 3 - monitor_only:
            - 无价格倒挂
        """
        if not self.has_arbitrage:
            return "monitor_only"

        if self.arb_profit <= 0:
            # mid-price 倒挂但 ask 成本 ≥ 1 → 挂单候选
            return "limit_candidate"

        # ask 成本 < 1 → 检查两腿流动性
        # 使用到的两腿: Hard_NO 和 Easy_YES
        hard_no_sr = self._spread_rate(self.hard_no_bid, self.hard_no_ask)
        easy_yes_sr = self._spread_rate(self.easy_best_bid, self.easy_best_ask)

        legs_liquid = True
        if hard_no_sr is not None and hard_no_sr > self.max_spread_rate:
            legs_liquid = False
        if easy_yes_sr is not None and easy_yes_sr > self.max_spread_rate:
            legs_liquid = False

        if legs_liquid:
            return "executable"

        # 有利润但流动性不足
        return "limit_candidate"


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
    yes_price: float = 0.0          # 用于比较的主价格（mid 或 outcomePrices）
    value_type: str = "unknown"     # price, fdv, percentage, quantity

    # YES 代币盘口价格
    best_bid: Optional[float] = None   # YES 买一价
    best_ask: Optional[float] = None   # YES 卖一价
    mid_price: Optional[float] = None  # YES 中间价 (bid+ask)/2
    bid_ask_spread: Optional[float] = None  # YES 买卖价差 (ask-bid)
    has_liquidity: bool = True         # 是否有真实流动性（bid 和 ask 都存在）

    # NO 代币盘口价格（从 YES 盘口推导）
    # 二元市场: NO_bid = 1 - YES_ask, NO_ask = 1 - YES_bid
    no_bid: Optional[float] = None     # NO 买一价 = 1 - YES_ask
    no_ask: Optional[float] = None     # NO 卖一价 = 1 - YES_bid

    # 交易量（用于流动性过滤）
    volume: float = 0.0                # 24h 交易量（美元）


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

    # 区间/分桶标题正则 — 用于检测互斥的范围型子市场（如 IPO market cap buckets）
    # 紧凑格式: "$100-200B", "$750B-1T", "$1T-1.25T", "100-200", "50K-100K"
    RANGE_BUCKET_PATTERN = re.compile(
        r'\$?\d+(?:[.,]\d+)?\s*[BMKT]?\s*[-–]\s*\$?\d+(?:[.,]\d+)?\s*[BMKT]?',
        re.IGNORECASE
    )
    # 紧凑开放端点: "$600B+", "$1.5T+", "100+"
    OPEN_BUCKET_PATTERN = re.compile(
        r'\$?\d+(?:[.,]\d+)?\s*[BMKT]?\s*\+',
        re.IGNORECASE
    )
    # 自然语言区间: "between $100B and $200B", "between 100 and 200"
    BETWEEN_PATTERN = re.compile(
        r'between\s+\$?[\d,.]+\s*[BMKT]?\s+and\s+\$?[\d,.]+\s*[BMKT]?',
        re.IGNORECASE
    )
    # 自然语言开放端点: "$600B or greater", "$1T or more", "$500B or higher"
    OR_GREATER_PATTERN = re.compile(
        r'\$[\d,.]+\s*[BMKT]?\s+or\s+(?:greater|more|higher|above)',
        re.IGNORECASE
    )
    # 自然语言下限: "less than $100B", "under $100B", "below $100B" (在 bucket 上下文中)
    LESS_THAN_PATTERN = re.compile(
        r'(?:less\s+than|under|below)\s+\$[\d,.]+\s*[BMKT]?',
        re.IGNORECASE
    )

    @classmethod
    def _is_range_title(cls, title: str) -> bool:
        """检测单个标题是否为区间/分桶格式"""
        return bool(
            cls.RANGE_BUCKET_PATTERN.search(title)
            or cls.OPEN_BUCKET_PATTERN.search(title)
            or cls.BETWEEN_PATTERN.search(title)
            or cls.OR_GREATER_PATTERN.search(title)
        )

    @classmethod
    def is_range_bucket_event(cls, markets: List[Dict]) -> bool:
        """检测事件是否为互斥区间型（如 IPO market cap buckets）

        区间型事件的子市场彼此互斥（每个覆盖一个独立范围），
        不存在子集/超集的逻辑包含关系，不应做 LSA 配对。

        检测策略（多层）:
        1. 紧凑格式: "$100-200B", "$600B+", "100-200"
        2. 自然语言: "between $100B and $200B", "$600B or greater"
        3. 组合检测: "less than $X" + "or greater" 在同一事件内

        判断标准: 如果 ≥2 个子市场匹配区间模式，整个事件被视为区间型。
        """
        range_count = 0
        has_less_than = False
        has_or_greater = False

        for m in markets:
            title = m.get('question', m.get('title', ''))
            if cls._is_range_title(title):
                range_count += 1
            if cls.LESS_THAN_PATTERN.search(title):
                has_less_than = True
            if cls.OR_GREATER_PATTERN.search(title):
                has_or_greater = True
            if range_count >= 2:
                return True

        # "less than $X" + "$Y or greater" 在同一事件 = bucket endpoints
        if has_less_than and has_or_greater:
            return True
        # "between" + "less than" = bucket event
        if range_count >= 1 and has_less_than:
            return True

        return False

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
        # 使用关键词库（基于单词边界匹配，避免子串误匹配）
        direction = ComparisonKeywords.get_direction(title)
        if direction:
            return direction

        # 无明确方向关键词时不猜测，返回 None
        # 仅有 $ 价格但无 "above/top/over" 等方向词的标题不应被归类
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

        # 移除月份名称（避免 "june" vs "december" 降低相似度）
        month_names = TimeKeywords.MONTHS
        # 移除停用词和月份
        words = text.lower().split()
        words = [w for w in words if w not in self.STOP_WORDS and w not in month_names and len(w) > 1]

        # 去重并排序
        words = sorted(set(words))

        return ' '.join(words)

    def get_value_type(self, title: str) -> str:
        """判断数值类型"""
        return ValueKeywords.get_value_type(title)

    def parse_submarket(self, market: Dict) -> Optional[SubMarket]:
        """
        解析子市场为结构化数据

        过滤条件:
        - closed=True 或 active=False 的子市场跳过（已结算）

        定价策略 (三层优先级):
        1. mid-price = (bestBid + bestAsk) / 2  — 最准确，反映盘口中位
        2. outcomePrices[0]  — Polymarket 的全局快照价，≈ mid-price
        3. bestAsk (仅在 bestBid 不可用时回退) — 最保守但可能失真

        NO 代币价格推导（二元市场性质）:
        - NO_bid = 1 - YES_ask
        - NO_ask = 1 - YES_bid

        Args:
            market: Polymarket 市场字典，包含 conditionId, question, bestAsk 等
        """
        # === 过滤已结算的子市场 ===
        # closed=True 表示该子市场已提前结算（即使父事件仍 active）
        # active=False 表示该子市场已不再活跃
        if market.get('closed') is True or market.get('active') is False:
            return None

        title = market.get('question', market.get('title', ''))
        if not title:
            return None

        comparison = self.extract_comparison(title)
        # 时间窗口型市场（"by December 31" 等）可能没有价格比较方向
        # 只要有日期或年份信息就允许解析，使用 "time" 标记
        if not comparison:
            if TimeKeywords.has_time_constraint(title):
                comparison = 'time'  # 特殊标记：仅用于时间窗口配对
            elif (self.extract_threshold(title) is not None
                  or self.extract_percentage(title) is not None):
                comparison = 'unknown'  # 有阈值但无方向关键词 → monitor 候选
            else:
                return None  # 无方向、无时间、无阈值 → 无法配对

        # === 解析盘口价格 ===
        best_bid = None
        best_ask = None

        raw_bid = market.get('bestBid')
        raw_ask = market.get('bestAsk')
        if raw_bid is not None:
            try:
                best_bid = float(raw_bid)
                if best_bid <= 0 or best_bid >= 1:
                    best_bid = None  # 无效值（0 或 1 代表无订单）
            except (ValueError, TypeError):
                best_bid = None
        if raw_ask is not None:
            try:
                best_ask = float(raw_ask)
                if best_ask <= 0 or best_ask >= 1:
                    best_ask = None  # 无效值
            except (ValueError, TypeError):
                best_ask = None

        # 计算 mid-price 和流动性
        mid_price = None
        bid_ask_spread = None
        has_liquidity = False

        if best_bid is not None and best_ask is not None:
            mid_price = (best_bid + best_ask) / 2
            bid_ask_spread = best_ask - best_bid
            has_liquidity = True

        # === 确定主价格（用于套利检测）===
        # 优先: mid-price > outcomePrices > bestAsk > price
        yes_price = 0.0

        if mid_price is not None and mid_price > 0:
            # 最佳: 有真实盘口的 mid-price
            yes_price = mid_price
        elif market.get('outcomePrices'):
            # 次佳: Polymarket 全局快照价（≈ mid-price）
            try:
                op_raw = market.get('outcomePrices', '[]')
                outcome_prices = json.loads(op_raw) if isinstance(op_raw, str) else op_raw
                if outcome_prices and float(outcome_prices[0]) > 0:
                    yes_price = float(outcome_prices[0])
                    # outcomePrices 也近似 mid，标记为有参考价格
                    if mid_price is None:
                        mid_price = yes_price
            except (json.JSONDecodeError, ValueError, IndexError):
                pass
        elif best_ask is not None and best_ask > 0:
            # 回退: 只有 ask 没有 bid（流动性极差）
            yes_price = best_ask
            mid_price = best_ask
        elif market.get('price') is not None:
            try:
                yes_price = float(market['price'])
                mid_price = yes_price
            except (ValueError, TypeError):
                pass

        if yes_price <= 0:
            return None  # 无有效价格，跳过

        # === 推导 NO 代币价格 ===
        # 二元市场性质: 买 NO = 卖 YES, 卖 NO = 买 YES
        # NO_bid = 1 - YES_ask (对手方卖 YES 即买 NO)
        # NO_ask = 1 - YES_bid (对手方买 YES 即卖 NO)
        no_bid = (1.0 - best_ask) if best_ask is not None else None
        no_ask = (1.0 - best_bid) if best_bid is not None else None

        # === 提取 24h 交易量 ===
        volume = 0.0
        try:
            volume = float(market.get('volume24hr', 0) or 0)
        except (ValueError, TypeError):
            volume = 0.0

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
            value_type=self.get_value_type(title),
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=mid_price,
            bid_ask_spread=bid_ask_spread,
            has_liquidity=has_liquidity,
            no_bid=no_bid,
            no_ask=no_ask,
            volume=volume,
        )

    def find_price_threshold_pairs_in_event(
        self,
        submarkets: List[SubMarket],
        event_id: str,
        event_title: str,
        event_slug: str = ""
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

        # 只保留有阈值且有价格方向的子市场（排除 'time' 标记的时间窗口市场）
        with_threshold = [s for s in submarkets if s.threshold is not None and s.comparison in ('>', '<')]

        # 按阈值排序
        with_threshold.sort(key=lambda s: s.threshold or 0)

        # === 价格梯度方向推断（辅助方法）===
        # 仅在关键词无法判断方向时使用（comparison 对双组合定价影响有限，
        # 因为 calculate_spread() 会自动检测两个方向的组合）。
        # 价格梯度用于改善 hard/easy 标签的准确性（纯展示用途）。
        #
        # 原理（3+ 同方向子市场时有效）:
        #   - 价格随阈值递减 → ">" 方向
        #   - 价格随阈值递增 → "<" 方向
        #
        # 注意: 不覆盖已有关键词方向。某些市场（如失业率预测）
        # 中间阈值概率最高，首尾不单调，梯度推断会出错。

        # 两两比较
        for i in range(len(with_threshold)):
            for j in range(i + 1, len(with_threshold)):
                s1 = with_threshold[i]
                s2 = with_threshold[j]

                # 方向必须一致（使用关键词方向，不做梯度覆盖）
                dir1 = s1.comparison
                dir2 = s2.comparison
                if dir1 != dir2:
                    continue

                effective_dir = dir1

                # 数值类型应该相同（price vs price, percentage vs percentage）
                if s1.value_type != s2.value_type and s1.value_type != 'unknown' and s2.value_type != 'unknown':
                    continue

                # 确定哪个是 hard/easy（使用推断方向）
                if effective_dir == '>':
                    # ">" 方向：阈值大的更难（如 BTC > $100k 比 > $50k 难）
                    if s1.threshold < s2.threshold:
                        hard, easy = s2, s1
                    else:
                        hard, easy = s1, s2
                else:  # "<" 方向
                    # "<" 方向：阈值小的更难（如 drop to 20% 比 drop to 40% 难）
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
                    relationship_desc=f"{value_type_name} ({effective_dir}): {self._format_threshold(hard.threshold)} vs {self._format_threshold(easy.threshold)}",
                    platform="polymarket",
                    hard_threshold=hard.threshold,
                    easy_threshold=easy.threshold,
                    comparison=effective_dir,
                    event_id=event_id,
                    event_title=event_title,
                    event_slug=event_slug,
                    hard_price=hard.yes_price,
                    easy_price=easy.yes_price,
                    value_type=hard.value_type,
                    **self._bid_ask_fields(hard, easy),
                )

                pair.calculate_spread()
                pairs.append(pair)

        return pairs

    def find_time_window_pairs_in_event(
        self,
        submarkets: List[SubMarket],
        event_id: str,
        event_title: str,
        event_slug: str = ""
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

                # 不同阈值交给 price_threshold 处理
                if (s1.threshold is not None and s2.threshold is not None
                        and s1.threshold != s2.threshold):
                    continue

                # 年份型需要高相似度：问题必须几乎相同，只有年份不同
                if not self._are_titles_similar(s1, s2, min_similarity=0.85):
                    continue

                # 只有累积截止型（by/before）才构成子集关系
                # "in 2025" vs "in 2026" 是不相交事件，不能套利
                title1_lower = s1.title.lower()
                title2_lower = s2.title.lower()
                has_cumulative = any(
                    kw in title1_lower or kw in title2_lower
                    for kw in ('by ', 'before ', 'end of ')
                )
                has_disjoint = any(
                    kw in title1_lower and kw in title2_lower
                    for kw in (' in ',)
                )
                if has_disjoint and not has_cumulative:
                    continue  # "in 2025" vs "in 2026" 不具备逻辑包含关系

                # 早期是 hard，晚期是 easy（by March 比 by December 更难）
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
                    event_slug=event_slug,
                    hard_price=hard.yes_price,
                    easy_price=easy.yes_price,
                    value_type="time",
                    **self._bid_ask_fields(hard, easy),
                )

                pair.calculate_spread()
                pairs.append(pair)

        # 日期型比较（如 "by Dec 31" vs "by Mar 31"）
        # 注意：需要更高的相似度要求，避免匹配完全不同的事件
        if len(with_date) >= 2:
            # 按月份+日期排序（确保同月内按日期排序）
            month_order = {
                'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
                'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12
            }

            def get_date_key(s: SubMarket) -> tuple:
                """提取 (年份, 月份, 日期) 排序键，确保同月内按日排序"""
                year = 9999
                month = 999
                day = 999
                if s.date_str:
                    ds = s.date_str.lower()
                    for m_name, m_num in month_order.items():
                        if m_name in ds:
                            month = m_num
                            break
                    # 提取日期数字（如 "March 31, 2026" 中的 31）
                    day_match = re.search(r'\b(\d{1,2})\b', s.date_str)
                    if day_match:
                        d = int(day_match.group(1))
                        if 1 <= d <= 31:
                            day = d
                    # 提取年份
                    year_match = re.search(r'\b(20[2-9]\d)\b', s.date_str)
                    if year_match:
                        year = int(year_match.group(1))
                return (year, month, day)

            with_date.sort(key=get_date_key)

            for i in range(len(with_date)):
                for j in range(i + 1, len(with_date)):
                    s1 = with_date[i]
                    s2 = with_date[j]

                    # 日期必须不同（相同日期无时间窗口可言）
                    if get_date_key(s1) == get_date_key(s2):
                        continue

                    # 关键：比较方向必须一致（不能一个 reach 一个 dip）
                    if s1.comparison != s2.comparison:
                        continue

                    # 如果两个市场有不同的阈值，交给 price_threshold 处理
                    # 例: "ETH > $3,500 by Dec" vs "ETH > $5,000 by Dec" 是 price_threshold
                    if (s1.threshold is not None and s2.threshold is not None
                            and s1.threshold != s2.threshold):
                        continue

                    # 日期型需要高相似度：问题必须几乎相同，只有日期不同
                    # 避免匹配不同实体（如 Google vs OpenAI, UFC 选手A vs 选手B）
                    if not self._are_titles_similar(s1, s2, min_similarity=0.85):
                        continue

                    # 只有累积截止型（by/before）才构成子集关系
                    # "on February 3" vs "on February 26" 是不相交的具体日期事件
                    # "in June" vs "in September" 也是不相交的（只能发生在某个月）
                    t1_lower = s1.title.lower()
                    t2_lower = s2.title.lower()
                    has_cumulative = any(
                        kw in t1_lower or kw in t2_lower
                        for kw in ('by ', 'before ', 'end of ')
                    )
                    has_disjoint = any(
                        kw in t1_lower and kw in t2_lower
                        for kw in (' on ', ' in ')
                    )
                    if has_disjoint and not has_cumulative:
                        continue

                    # 排序后 s1 日期 <= s2 日期
                    # 早期截止 = 更难（子集），晚期截止 = 更容易（超集）
                    # 例: "by March 15" (hard) vs "by March 31" (easy)
                    hard, easy = s1, s2

                    pair = EventPair(
                        hard_market_id=hard.market_id,
                        hard_title=hard.title,
                        easy_market_id=easy.market_id,
                        easy_title=easy.title,
                        logical_type=LogicalType.TIME_WINDOW,
                        relationship_desc=f"时间窗口: {hard.date_str} vs {easy.date_str}",
                        platform="polymarket",
                        hard_threshold=0.0,
                        easy_threshold=0.0,
                        comparison="earlier",
                        event_id=event_id,
                        event_title=event_title,
                        event_slug=event_slug,
                        hard_price=hard.yes_price,
                        easy_price=easy.yes_price,
                        value_type="time",
                        **self._bid_ask_fields(hard, easy),
                    )

                    pair.calculate_spread()
                    pairs.append(pair)

        return pairs

    @staticmethod
    def _bid_ask_fields(hard: SubMarket, easy: SubMarket) -> Dict:
        """从 SubMarket 提取盘口字段，传给 EventPair 构造

        包含 YES 和 NO 代币的完整报价以及交易量。
        NO 价格由 parse_submarket() 从 YES 盘口推导。
        """
        return {
            # YES 代币盘口
            'hard_best_bid': hard.best_bid,
            'hard_best_ask': hard.best_ask,
            'hard_mid': hard.mid_price,
            'hard_spread': hard.bid_ask_spread,
            'easy_best_bid': easy.best_bid,
            'easy_best_ask': easy.best_ask,
            'easy_mid': easy.mid_price,
            'easy_spread': easy.bid_ask_spread,
            'hard_has_liquidity': hard.has_liquidity,
            'easy_has_liquidity': easy.has_liquidity,
            # NO 代币盘口（推导）
            'hard_no_bid': hard.no_bid,
            'hard_no_ask': hard.no_ask,
            'easy_no_bid': easy.no_bid,
            'easy_no_ask': easy.no_ask,
            # 交易量
            'hard_volume': hard.volume,
            'easy_volume': easy.volume,
        }

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

    def find_monitor_pairs_in_event(
        self,
        submarkets: List[SubMarket],
        event_id: str,
        event_title: str,
        event_slug: str = "",
        existing_pair_keys: Set[str] = None
    ) -> List[EventPair]:
        """宽松条件的 monitor 层配对 — 兜底严格逻辑之外的潜在机会

        与严格配对的区别:
        1. 包含 comparison='unknown' 的市场（有阈值但无方向关键词）
        2. 阈值差异门槛降低（5% vs 严格的 10%）
        3. 使用价格梯度推断方向（仅限 monitor 层，不影响 exec/limit）
        4. 所有结果强制 signal_tier='monitor_only'

        Args:
            existing_pair_keys: 严格配对已生成的 pair_key 集合（用于去重）
        """
        if existing_pair_keys is None:
            existing_pair_keys = set()

        pairs = []
        MONITOR_MIN_DIFF_PCT = 5.0  # 宽松阈值差异（严格层为 10%）

        # 所有有阈值的子市场（包括 '>', '<', 'unknown'）
        with_threshold = [
            s for s in submarkets
            if s.threshold is not None and s.comparison in ('>', '<', 'unknown')
        ]
        if len(with_threshold) < 2:
            return []

        # 按阈值排序
        with_threshold.sort(key=lambda s: s.threshold or 0)

        for i in range(len(with_threshold)):
            for j in range(i + 1, len(with_threshold)):
                s1 = with_threshold[i]  # 低阈值
                s2 = with_threshold[j]  # 高阈值

                # 数值类型应该相同
                if (s1.value_type != s2.value_type
                        and s1.value_type != 'unknown'
                        and s2.value_type != 'unknown'):
                    continue

                # 阈值差异（宽松）
                if s1.threshold > 0 and s2.threshold > 0:
                    diff_pct = abs(s2.threshold - s1.threshold) / min(s1.threshold, s2.threshold) * 100
                    if diff_pct < MONITOR_MIN_DIFF_PCT:
                        continue

                # === 方向推断 ===
                # 优先使用关键词方向；如果两边都有且一致，直接用
                dir1 = s1.comparison if s1.comparison in ('>', '<') else None
                dir2 = s2.comparison if s2.comparison in ('>', '<') else None

                if dir1 and dir2 and dir1 != dir2:
                    continue  # 方向冲突，跳过

                effective_dir = dir1 or dir2  # 至少一边有关键词方向

                if not effective_dir:
                    # 两边都是 unknown → 用价格梯度推断
                    # 低阈值价格 > 高阈值价格 → '>'（高阈值更难，概率更低）
                    # 低阈值价格 < 高阈值价格 → '<'（低阈值更难，概率更低）
                    if abs(s1.yes_price - s2.yes_price) < 0.01:
                        continue  # 价格太接近，无法判断
                    effective_dir = '>' if s1.yes_price > s2.yes_price else '<'

                # 确定 hard/easy
                if effective_dir == '>':
                    hard, easy = s2, s1  # 高阈值更难
                else:
                    hard, easy = s1, s2  # 低阈值更难

                # 去重：跳过严格层已找到的配对
                pair_key = f"price_threshold:{hard.market_id}:{easy.market_id}"
                if pair_key in existing_pair_keys:
                    continue

                value_type_name = {
                    'fdv': 'FDV', 'percentage': '百分比',
                    'quantity': '数量', 'price': '价格',
                }.get(hard.value_type, '阈值')

                pair = EventPair(
                    hard_market_id=hard.market_id,
                    hard_title=hard.title,
                    easy_market_id=easy.market_id,
                    easy_title=easy.title,
                    logical_type=LogicalType.PRICE_THRESHOLD,
                    relationship_desc=f"{value_type_name} ({effective_dir}): "
                                      f"{self._format_threshold(hard.threshold)} vs "
                                      f"{self._format_threshold(easy.threshold)}",
                    platform="polymarket",
                    hard_threshold=hard.threshold,
                    easy_threshold=easy.threshold,
                    comparison=effective_dir,
                    event_id=event_id,
                    event_title=event_title,
                    event_slug=event_slug,
                    hard_price=hard.yes_price,
                    easy_price=easy.yes_price,
                    value_type=hard.value_type,
                    **self._bid_ask_fields(hard, easy),
                )

                pair.calculate_spread()

                # Monitor 层：只保留有 mid-price 倒挂的
                if pair.has_arbitrage:
                    pair.signal_tier = 'monitor_only'  # 强制 monitor
                    pairs.append(pair)

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
        elif value >= 0.01:
            return f"{value:.2f}%"
        else:
            return f"{value}"


class LogicalSpreadArbitrageDetector:
    """逻辑价差套利检测器（主类）"""

    # 最低组合交易量（24h 美元），两个市场 volume 之和低于此值则跳过
    MIN_COMBINED_VOLUME = 1000.0

    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.analyzer = LogicalSpreadAnalyzer(config)
        self.logger = logger

        lsa_config = self.config.get('logical_spread_arbitrage', {})
        self.min_spread_threshold = lsa_config.get('min_spread_threshold', 0.0)
        self.fee_rate = lsa_config.get('fee_rate', 0.02)
        self.min_combined_volume = lsa_config.get(
            'min_combined_volume', self.MIN_COMBINED_VOLUME
        )

        self._cached_pairs: List[EventPair] = []

    def scan_events(
        self,
        events: List[Dict],
        platform: str = "polymarket"
    ) -> List[EventPair]:
        """
        扫描事件列表，检测逻辑价差套利机会（两轮扫描）

        第一轮（严格）: 关键词方向确定 → executable / limit_candidate
        第二轮（宽松）: 包含无方向关键词的市场 → monitor_only

        Args:
            events: 从 /events API 获取的事件列表
                   每个事件包含 id, title, markets[] 等字段
            platform: 平台名称

        Returns:
            检测到的套利机会列表（按 tier 排序）
        """
        strict_pairs = []
        monitor_pairs = []

        for event in events:
            event_id = event.get('id', '')
            event_title = event.get('title', event.get('slug', ''))
            event_slug = event.get('slug', '')
            markets = event.get('markets', [])

            if not markets or len(markets) < 2:
                continue

            # 跳过互斥区间型事件（如 IPO market cap buckets: <$100B, $100-200B, ...）
            if self.analyzer.is_range_bucket_event(markets):
                continue

            # 解析子市场（包含 comparison='unknown' 的）
            submarkets = []
            for market in markets:
                submarket = self.analyzer.parse_submarket(market)
                if submarket:
                    submarkets.append(submarket)

            if len(submarkets) < 2:
                continue

            # === 第一轮：严格配对（executable / limit_candidate）===
            price_pairs = self.analyzer.find_price_threshold_pairs_in_event(
                submarkets, event_id, event_title, event_slug
            )
            time_pairs = self.analyzer.find_time_window_pairs_in_event(
                submarkets, event_id, event_title, event_slug
            )
            strict_pairs.extend(price_pairs)
            strict_pairs.extend(time_pairs)

            # === 第二轮：宽松配对（monitor_only）===
            # 收集严格层已配对的 key，避免重复
            existing_keys = {p.pair_key for p in price_pairs + time_pairs}
            monitor = self.analyzer.find_monitor_pairs_in_event(
                submarkets, event_id, event_title, event_slug, existing_keys
            )
            monitor_pairs.extend(monitor)

        # 过滤：只保留有套利机会的
        strict_arb = [p for p in strict_pairs if p.has_arbitrage]
        monitor_arb = [p for p in monitor_pairs if p.has_arbitrage]

        # 过滤：极薄流动性市场（两个子市场 24h 交易量之和 < 阈值）
        if self.min_combined_volume > 0:
            strict_arb = [
                p for p in strict_arb
                if (p.hard_volume + p.easy_volume) >= self.min_combined_volume
            ]
            # Monitor 层用更宽松的交易量门槛（严格层的一半）
            monitor_vol_min = self.min_combined_volume / 2
            monitor_arb = [
                p for p in monitor_arb
                if (p.hard_volume + p.easy_volume) >= monitor_vol_min
            ]

        # 合并
        all_arbitrage = strict_arb + monitor_arb

        self._cached_pairs = all_arbitrage
        strict_count = len(strict_arb)
        monitor_count = len(monitor_arb)
        self.logger.info(
            f"[LogicalSpread] 扫描 {len(events)} 个事件，"
            f"检测到 {strict_count} 个严格套利 + {monitor_count} 个 monitor 机会"
        )

        return all_arbitrage

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
