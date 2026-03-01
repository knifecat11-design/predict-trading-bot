# -*- coding: utf-8 -*-
"""
Logical Spread Arbitrage Module - 逻辑价差套利检测器

核心原理：
对于具有逻辑包含关系的两个事件 A（较难/子集）和 B（较易/超集）：
- 正常情况：P(A) < P(B)（较难的事件概率更低）
- 套利机会：当 P(A) ≥ P(B) 时（市场倒挂或定价异常）
  - 当 P(A) > P(B)：明确的市场倒挂，成本 < 1
  - 当 P(A) = P(B)：定价异常，难度差异未反映，成本 = 1（关注潜在机会）
  - 策略：买入 A 的 NO + 买入 B 的 YES

支持的逻辑关系类型：
1. PRICE_THRESHOLD: 价格阈值 (BTC>$100k ⊆ BTC>$50k)
2. TIME_WINDOW: 时间窗口 (2025年达成 ⊆ 2026年达成)
3. MULTI_OUTCOME: 多结果事件的分解关系
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set, Literal
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class LogicalType(Enum):
    """逻辑关系类型"""
    PRICE_THRESHOLD = "price_threshold"  # 价格阈值包含
    TIME_WINDOW = "time_window"          # 时间窗口包含
    CONDITIONAL = "conditional"          # 条件层级
    MULTI_OUTCOME = "multi_outcome"      # 多结果分解


@dataclass
class EventPair:
    """逻辑事件对"""
    # 基础信息（必需字段，无默认值）
    hard_market_id: str        # 较难事件的市场ID（条件更严格）
    hard_title: str            # 较难事件的标题
    easy_market_id: str        # 较易事件的市场ID（条件更宽松）
    easy_title: str            # 较易事件的标题

    # 价格信息（可选字段，有默认值）
    hard_price: float = 0.0    # 较难事件的YES价格
    easy_price: float = 0.0    # 较易事件的YES价格

    # 逻辑关系
    logical_type: LogicalType = LogicalType.PRICE_THRESHOLD
    relationship_desc: str = ""  # 关系描述，如 "更高价格阈值"

    # 套利信息
    spread: float = 0.0         # 价差 = hard_price - easy_price
    arbitrage_cost: float = 0.0 # 套利成本
    arbitrage_profit: float = 0.0 # 套利利润（未扣费）
    has_arbitrage: bool = False # 是否存在套利机会

    # 元数据
    platform: str = "polymarket"
    detected_at: str = ""
    hard_threshold: Optional[float] = None  # 阈值（用于价格类型）
    easy_threshold: Optional[float] = None

    def __post_init__(self):
        if not self.detected_at:
            self.detected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @property
    def pair_key(self) -> str:
        """生成唯一键用于冷却去重"""
        return f"{self.logical_type.value}:{self.hard_market_id}:{self.easy_market_id}"

    def calculate_spread(self) -> None:
        """计算价差和套利收益"""
        self.spread = self.hard_price - self.easy_price

        # 当 hard_price >= easy_price 时存在倒挂
        # 原理：hard 事件难度更高，正常情况下 P(hard) < P(easy)
        # 即使 P(hard) = P(easy) 也是不合理的定价（难度差异未反映）
        # 策略：买入 hard 的 NO + 买入 easy 的 YES
        if self.spread >= 0:
            self.has_arbitrage = True
            self.arbitrage_cost = (1 - self.hard_price) + self.easy_price
            self.arbitrage_profit = 1 - self.arbitrage_cost
        else:
            self.has_arbitrage = False
            self.arbitrage_cost = 0
            self.arbitrage_profit = 0


@dataclass
class ThresholdMatch:
    """阈值匹配结果"""
    entity: str           # 实体名，如 "bitcoin"
    hard_market_id: str
    hard_title: str
    hard_threshold: float
    easy_market_id: str
    easy_title: str
    easy_threshold: float
    comparison: str       # ">", "<", ">=", "<="


class EventPairExtractor:
    """
    事件对提取器

    从市场列表中识别具有逻辑包含关系的事件对
    """

    # 实体关键词（用于分组）
    ENTITY_KEYWORDS = {
        'bitcoin': r'\b(?:Bitcoin|BTC)\b',
        'ethereum': r'\b(?:Ethereum|ETH)\b',
        'solana': r'\b(?:Solana|SOL)\b',
        'xrp': r'\b(?:XRP|Ripple)\b',
        'bnb': r'\b(?:BNB|Binance\s+Coin)\b',
        'trump': r'\bTrump\b',
        'fed': r'\b(?:Federal\s+Reserve|Fed)\b',
        'sp500': r'\b(?:S&P\s+500|SPX|SP500)\b',
        'nasdaq': r'\bNasdaq\b',
    }

    # 价格提取模式（支持 k/m/b/t 后缀）
    PRICE_PATTERNS = [
        r'\$([\d,]+(?:\.\d+)?)[kKmMbBtT]?',  # $100k, $1.5M
    ]

    # 年份提取
    YEAR_PATTERN = r'\b(20[2-9][0-9])\b'

    # 阈值比较词
    THRESHOLD_OPS = {
        'above': '>',
        'below': '<',
        'over': '>',
        'under': '<',
        'exceeds': '>',
        'hits': '>=',
        'reaches': '>=',
        'tops': '>',
        'surpasses': '>',
        'falls below': '<',
        'drops below': '<',
    }

    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.logger = logger

    def extract_price_threshold(self, title: str) -> Optional[float]:
        """从标题中提取价格阈值"""
        if not title:
            return None

        title_lower = title.lower()

        # 尝试各种价格模式
        for pattern in self.PRICE_PATTERNS:
            matches = re.findall(pattern, title, re.IGNORECASE)
            if matches:
                try:
                    price_str = matches[0].replace(',', '')
                    price = float(price_str)

                    # 检查后缀 - 必须紧邻数字或在$之后
                    # 匹配 $100k, $1.5m, $2b 等格式
                    suffix_match = re.search(r'\$[\d,]+(?:\.\d+)?([kmbt])', title_lower)
                    if suffix_match:
                        suffix = suffix_match.group(1)
                        if suffix == 'k':
                            price *= 1000
                        elif suffix == 'm':
                            price *= 1000000
                        elif suffix == 'b':
                            price *= 1000000000
                        elif suffix == 't':
                            price *= 1000000000000
                    # 如果没有后缀且数字较大（如 $100,000），直接使用

                    return price
                except (ValueError, IndexError):
                    continue

        return None

    def extract_year(self, title: str) -> Optional[int]:
        """从标题中提取年份"""
        if not title:
            return None

        matches = re.findall(self.YEAR_PATTERN, title)
        if matches:
            try:
                return int(matches[0])
            except ValueError:
                pass

        return None

    def detect_entity(self, title: str) -> Optional[str]:
        """检测标题中的实体"""
        if not title:
            return None

        title_lower = title.lower()

        for entity, pattern in self.ENTITY_KEYWORDS.items():
            if re.search(pattern, title, re.IGNORECASE):
                return entity

        return None

    def group_by_entity(
        self,
        markets: List[Dict]
    ) -> Dict[str, List[Dict]]:
        """按实体分组市场"""
        groups = {}

        for market in markets:
            title = market.get('title', market.get('question', ''))
            entity = self.detect_entity(title)

            if entity:
                if entity not in groups:
                    groups[entity] = []
                groups[entity].append(market)

        return groups

    def find_price_threshold_pairs(
        self,
        markets: List[Dict],
        min_threshold_diff_pct: float = 10.0
    ) -> List[ThresholdMatch]:
        """
        查找价格阈值型事件对

        例如：
        - "Bitcoin > $100k in 2025" (hard)
        - "Bitcoin > $50k in 2025" (easy)
        """
        pairs = []

        # 按实体分组
        entity_groups = self.group_by_entity(markets)

        for entity, group in entity_groups.items():
            # 提取每个市场的阈值
            with_thresholds = []
            for market in group:
                title = market.get('title', market.get('question', ''))
                threshold = self.extract_price_threshold(title)
                if threshold and threshold > 0:
                    with_thresholds.append({
                        'market': market,
                        'title': title,
                        'threshold': threshold,
                        'id': market.get('id', market.get('conditionId', ''))
                    })

            # 按阈值排序
            with_thresholds.sort(key=lambda x: x['threshold'])

            # 查找阈值对
            for i in range(len(with_thresholds)):
                for j in range(i + 1, len(with_thresholds)):
                    lower = with_thresholds[i]
                    higher = with_thresholds[j]

                    # 计算阈值差异百分比
                    diff_pct = (higher['threshold'] / lower['threshold'] - 1) * 100

                    if diff_pct >= min_threshold_diff_pct:
                        pairs.append(ThresholdMatch(
                            entity=entity,
                            hard_market_id=higher['id'],
                            hard_title=higher['title'],
                            hard_threshold=higher['threshold'],
                            easy_market_id=lower['id'],
                            easy_title=lower['title'],
                            easy_threshold=lower['threshold'],
                            comparison=">"
                        ))

        return pairs

    def find_time_window_pairs(
        self,
        markets: List[Dict]
    ) -> List[ThresholdMatch]:
        """
        查找时间窗口型事件对

        例如：
        - "Trump president in 2025" (hard，时间窗口更短)
        - "Trump president in 2026" (easy，时间窗口更长)
        """
        pairs = []

        entity_groups = self.group_by_entity(markets)

        for entity, group in entity_groups.items():
            # 提取年份
            with_years = []
            for market in group:
                title = market.get('title', market.get('question', ''))
                year = self.extract_year(title)
                if year:
                    with_years.append({
                        'market': market,
                        'title': title,
                        'year': year,
                        'id': market.get('id', market.get('conditionId', ''))
                    })

            # 按年份排序
            with_years.sort(key=lambda x: x['year'])

            # 查找相邻年份对
            for i in range(len(with_years) - 1):
                earlier = with_years[i]
                later = with_years[i + 1]

                # 只选择相邻年份（避免 2025 vs 2027 这样跨度太大的）
                if later['year'] - earlier['year'] <= 2:
                    pairs.append(ThresholdMatch(
                        entity=entity,
                        hard_market_id=earlier['id'],
                        hard_title=earlier['title'],
                        hard_threshold=float(earlier['year']),
                        easy_market_id=later['id'],
                        easy_title=later['title'],
                        easy_threshold=float(later['year']),
                        comparison="earlier"
                    ))

        return pairs


class SpreadCalculator:
    """价差计算器"""

    @staticmethod
    def calculate_arbitrage(
        hard_price: float,
        easy_price: float,
        fee_rate: float = 0.02
    ) -> Dict[str, float]:
        """
        计算套利收益

        当 hard_price >= easy_price 时存在倒挂：
        - 原理：hard 事件难度更高，正常情况下 P(hard) < P(easy)
        - 即使 P(hard) = P(easy) 也是不合理的定价（难度差异未反映）
        - 买入 hard 的 NO (成本: 1 - hard_price)
        - 买入 easy 的 YES (成本: easy_price)
        - 总成本: (1 - hard_price) + easy_price
        - 收益: 1 - 总成本

        Args:
            hard_price: 较难事件的YES价格
            easy_price: 较易事件的YES价格
            fee_rate: 交易费率（默认2%）

        Returns:
            包含 spread, cost, profit, net_profit 的字典
        """
        spread = hard_price - easy_price

        if spread >= 0:
            # 市场倒挂，存在套利机会（包括 spread=0 的情况）
            cost = (1 - hard_price) + easy_price
            profit = 1 - cost
            net_profit = profit - (fee_rate * 2)  # 双边交易费
        else:
            cost = 0
            profit = 0
            net_profit = 0

        return {
            'spread': spread,
            'cost': cost,
            'profit': profit,
            'net_profit': net_profit,
            'has_arbitrage': spread >= 0
        }


class LogicalSpreadArbitrageDetector:
    """
    逻辑价差套利检测器

    主控制器，整合事件对识别、价格监控和套利检测
    """

    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.extractor = EventPairExtractor(config)
        self.calculator = SpreadCalculator()
        self.logger = logger

        # 配置参数
        lsa_config = self.config.get('logical_spread_arbitrage', {})
        self.min_spread_threshold = lsa_config.get('min_spread_threshold', 0.5)  # 最小价差百分比
        self.fee_rate = lsa_config.get('fee_rate', 0.02)  # 交易费率
        self.min_threshold_diff_pct = lsa_config.get('min_threshold_diff_pct', 10.0)  # 阈值最小差异百分比

        # 已识别的事件对缓存
        self._cached_pairs: List[EventPair] = []
        self._pair_prices: Dict[str, Tuple[float, float]] = {}

    def detect_pairs(
        self,
        markets: List[Dict],
        platform: str = "polymarket"
    ) -> List[EventPair]:
        """
        检测市场中的逻辑事件对

        Args:
            markets: 市场列表
            platform: 平台名称

        Returns:
            EventPair 列表
        """
        pairs = []

        # 1. 查找价格阈值型事件对
        price_pairs = self.extractor.find_price_threshold_pairs(
            markets,
            min_threshold_diff_pct=self.min_threshold_diff_pct
        )

        for match in price_pairs:
            pair = EventPair(
                hard_market_id=match.hard_market_id,
                hard_title=match.hard_title,
                easy_market_id=match.easy_market_id,
                easy_title=match.easy_title,
                logical_type=LogicalType.PRICE_THRESHOLD,
                relationship_desc=f"价格阈值: {match.comparison} ${self._format_number(match.hard_threshold)} vs ${self._format_number(match.easy_threshold)}",
                platform=platform,
                hard_threshold=match.hard_threshold,
                easy_threshold=match.easy_threshold
            )
            pairs.append(pair)

        # 2. 查找时间窗口型事件对
        time_pairs = self.extractor.find_time_window_pairs(markets)

        for match in time_pairs:
            pair = EventPair(
                hard_market_id=match.hard_market_id,
                hard_title=match.hard_title,
                easy_market_id=match.easy_market_id,
                easy_title=match.easy_title,
                logical_type=LogicalType.TIME_WINDOW,
                relationship_desc=f"时间窗口: {int(match.hard_threshold)} vs {int(match.easy_threshold)}",
                platform=platform,
                hard_threshold=match.hard_threshold,
                easy_threshold=match.easy_threshold
            )
            pairs.append(pair)

        self._cached_pairs = pairs
        self.logger.info(f"[LogicalSpread] 检测到 {len(pairs)} 个事件对")

        return pairs

    def update_prices(
        self,
        price_dict: Dict[str, float]
    ) -> List[EventPair]:
        """
        更新事件对价格并检测套利机会

        Args:
            price_dict: 市场ID -> YES价格的映射

        Returns:
            存在套利机会的 EventPair 列表
        """
        arbitrage_pairs = []

        for pair in self._cached_pairs:
            # 获取价格
            hard_price = price_dict.get(pair.hard_market_id)
            easy_price = price_dict.get(pair.easy_market_id)

            if hard_price is None or easy_price is None:
                continue

            pair.hard_price = hard_price
            pair.easy_price = easy_price

            # 计算价差
            pair.calculate_spread()

            # 检查是否满足套利阈值
            if pair.has_arbitrage:
                spread_pct = pair.spread * 100
                if spread_pct >= self.min_spread_threshold:
                    arbitrage_pairs.append(pair)
                    self.logger.debug(
                        f"[LogicalSpread] 套利: {pair.hard_title[:30]}... "
                        f"价差={spread_pct:.2f}%"
                    )

        return arbitrage_pairs

    def scan_markets(
        self,
        markets: List[Dict],
        price_dict: Dict[str, float],
        platform: str = "polymarket"
    ) -> List[EventPair]:
        """
        完整扫描：检测事件对 + 更新价格 + 返回套利机会

        Args:
            markets: 市场列表
            price_dict: 市场ID -> YES价格的映射
            platform: 平台名称

        Returns:
            存在套利机会的 EventPair 列表
        """
        # 重新检测事件对（应对新市场）
        self.detect_pairs(markets, platform)

        # 更新价格并返回套利机会
        return self.update_prices(price_dict)

    def format_arbitrage_message(self, pair: EventPair) -> str:
        """格式化套利通知消息"""
        spread_pct = pair.spread * 100
        profit_pct = pair.arbitrage_profit * 100
        cost_pct = pair.arbitrage_cost * 100
        hard_yes_pct = pair.hard_price * 100
        easy_yes_pct = pair.easy_price * 100

        # 判断倒挂类型
        if spread_pct > 0:
            status_text = f"市场倒挂 (+{spread_pct:.2f}%)"
        else:  # spread_pct == 0
            status_text = f"定价异常 (价差为0，难度未反映)"

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
    def _format_number(num: float) -> str:
        """格式化数字显示"""
        if num >= 1_000_000_000:
            return f"{num/1_000_000_000:.1f}B"
        elif num >= 1_000_000:
            return f"{num/1_000_000:.1f}M"
        elif num >= 1_000:
            return f"{num/1_000:.1f}K"
        return f"{num:.0f}"

    @staticmethod
    def _get_type_name(logical_type: LogicalType) -> str:
        """获取逻辑类型中文名"""
        names = {
            LogicalType.PRICE_THRESHOLD: "价格阈值",
            LogicalType.TIME_WINDOW: "时间窗口",
            LogicalType.CONDITIONAL: "条件层级",
            LogicalType.MULTI_OUTCOME: "多结果分解",
        }
        return names.get(logical_type, "未知类型")


def create_logical_spread_detector(config: Dict) -> LogicalSpreadArbitrageDetector:
    """工厂函数：创建逻辑价差套利检测器"""
    return LogicalSpreadArbitrageDetector(config)
