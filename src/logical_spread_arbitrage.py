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
    """逻辑事件对"""
    hard_market_id: str
    hard_title: str
    easy_market_id: str
    easy_title: str

    hard_price: float = 0.0           # mid-price（用于检测）
    easy_price: float = 0.0           # mid-price（用于检测）

    logical_type: LogicalType = LogicalType.PRICE_THRESHOLD
    relationship_desc: str = ""

    spread: float = 0.0               # hard_mid - easy_mid
    arbitrage_cost: float = 0.0       # 基于 mid-price 的理论成本
    arbitrage_profit: float = 0.0     # 基于 mid-price 的理论利润
    has_arbitrage: bool = False

    # 基于 bestAsk 的实际执行成本（更保守）
    ask_cost: float = 0.0             # 基于 bestAsk 的实际买入成本
    ask_profit: float = 0.0           # 基于 bestAsk 的实际利润

    # 信号分层（两层策略）
    # "executable"      — bestAsk 直接有利可图，可立即执行
    # "limit_candidate" — mid-price 显示机会，适合挂限价单
    # "monitor_only"    — mid-price 有价差但流动性极差，仅监控
    signal_tier: str = "monitor_only"

    platform: str = "polymarket"
    detected_at: str = ""
    event_id: str = ""
    event_title: str = ""
    hard_threshold: Optional[float] = None
    easy_threshold: Optional[float] = None
    comparison: str = ""
    value_type: str = ""

    # 盘口详细数据（用于前端展示）
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

    def __post_init__(self):
        if not self.detected_at:
            self.detected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @property
    def pair_key(self) -> str:
        return f"{self.logical_type.value}:{self.hard_market_id}:{self.easy_market_id}"

    def calculate_spread(self) -> None:
        """计算价差和套利利润，并分层分类信号

        两层策略:
        1. executable      — bestAsk 两腿都有利可图，可立即市价执行
        2. limit_candidate — mid-price 显示机会但 bestAsk 不够，适合挂限价单
        3. monitor_only    — mid-price 有价差但流动性极差或价差极小，仅监控

        使用 mid-price 进行套利检测（更宽松，发现更多机会），
        同时计算 bestAsk 下的实际执行成本（更保守，反映真实成本）。
        """
        # 基于 mid-price 检测（主指标）
        self.spread = self.hard_price - self.easy_price
        if self.spread >= 0:
            self.has_arbitrage = True
            self.arbitrage_cost = (1 - self.hard_price) + self.easy_price
            self.arbitrage_profit = 1 - self.arbitrage_cost
        else:
            self.has_arbitrage = False
            self.arbitrage_cost = 0
            self.arbitrage_profit = 0

        # 基于 bestAsk 的实际执行成本
        # 策略: 买 Hard NO (= 1 - hard_ask) + 买 Easy YES (= easy_ask)
        hard_ask = self.hard_best_ask
        easy_ask = self.easy_best_ask
        if hard_ask is not None and easy_ask is not None and hard_ask > 0 and easy_ask > 0:
            self.ask_cost = (1 - hard_ask) + easy_ask
            self.ask_profit = 1 - self.ask_cost
        else:
            self.ask_cost = 0
            self.ask_profit = 0

        # === 信号分层 ===
        self.signal_tier = self._classify_signal_tier()

    def _classify_signal_tier(self) -> str:
        """分类信号层级

        Tier 1 - executable (即时可执行):
            - bestAsk 两腿都有效
            - ask_profit > 0（市价买入两腿后净赚）
            - 两腿都有流动性（bid 和 ask 都存在）

        Tier 2 - limit_candidate (挂单候选):
            - mid-price 显示套利机会（has_arbitrage=True, arbitrage_profit > 0）
            - bestAsk 利润不足或缺失（ask_profit <= 0 或无法计算）
            - 至少一腿有盘口数据
            - mid-price 在合理区间（盘口价差不超过 30%）

        Tier 3 - monitor_only (仅监控):
            - mid-price 有价差但流动性极差（两腿都无流动性）
            - 或者盘口价差过宽（> 30%），mid-price 参考意义有限
        """
        if not self.has_arbitrage:
            return "monitor_only"

        # Tier 1: bestAsk 直接有利可图
        if (self.ask_profit > 0 and
                self.hard_has_liquidity and self.easy_has_liquidity):
            return "executable"

        # 检查盘口价差是否在合理区间
        # 价差 > 30% 说明流动性极差，mid-price 参考意义不大
        MAX_REASONABLE_SPREAD = 0.30
        hard_spread_ok = (self.hard_spread is not None and
                          self.hard_spread <= MAX_REASONABLE_SPREAD)
        easy_spread_ok = (self.easy_spread is not None and
                          self.easy_spread <= MAX_REASONABLE_SPREAD)

        # Tier 2: mid-price 在合理区间，适合挂限价单
        if self.arbitrage_profit > 0:
            # 至少一腿有合理盘口
            if hard_spread_ok or easy_spread_ok:
                return "limit_candidate"
            # 两腿都没有盘口价差数据但 mid 有效（来自 outcomePrices）
            if (self.hard_spread is None and self.easy_spread is None and
                    self.hard_mid is not None and self.easy_mid is not None):
                return "limit_candidate"

        return "monitor_only"


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

    # 盘口详细价格
    best_bid: Optional[float] = None   # 买一价
    best_ask: Optional[float] = None   # 卖一价
    mid_price: Optional[float] = None  # 中间价 (bid+ask)/2
    bid_ask_spread: Optional[float] = None  # 买卖价差 (ask-bid)
    has_liquidity: bool = True         # 是否有真实流动性（bid 和 ask 都存在）


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

        定价策略 (三层优先级):
        1. mid-price = (bestBid + bestAsk) / 2  — 最准确，反映盘口中位
        2. outcomePrices[0]  — Polymarket 的全局快照价，≈ mid-price
        3. bestAsk (仅在 bestBid 不可用时回退) — 最保守但可能失真

        Args:
            market: Polymarket 市场字典，包含 conditionId, question, bestAsk 等
        """
        title = market.get('question', market.get('title', ''))
        if not title:
            return None

        comparison = self.extract_comparison(title)
        # 时间窗口型市场（"by December 31" 等）可能没有价格比较方向
        # 只要有日期或年份信息就允许解析，使用 "time" 标记
        if not comparison:
            if TimeKeywords.has_time_constraint(title):
                comparison = 'time'  # 特殊标记：仅用于时间窗口配对
            else:
                return None

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

        # 只保留有阈值且有价格方向的子市场（排除 'time' 标记的时间窗口市场）
        with_threshold = [s for s in submarkets if s.threshold is not None and s.comparison in ('>', '<')]

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

                    # 检查基础问题相似度（同事件内的日期型，阈值可以宽松）
                    if not self._are_titles_similar(s1, s2, min_similarity=0.5):
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
                        hard_threshold=0.0,
                        easy_threshold=0.0,
                        comparison="earlier",
                        event_id=event_id,
                        event_title=event_title,
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
        """从 SubMarket 提取盘口字段，传给 EventPair 构造"""
        return {
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
