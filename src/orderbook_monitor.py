# -*- coding: utf-8 -*-
"""
Polymarket Orderbook Price Monitor - 订单簿价格监控模块

功能：
1. 获取市场订单簿数据（买一至买五、卖一至卖五的价格和数量）
2. 计算真实可执行成本（考虑订单簿深度）
3. 检测流动性不足的市场（建议挂单而非直接吃单）

核心算法：
- 保守算法：取卖一价，如果深度不足则累加卖二、卖三...
- 计算加权平均成本
- 当组合成本 < 1 时触发套利警报
"""

import logging
import time
import requests
from typing import Dict, List, Optional, Tuple, Union
from dataclasses import dataclass
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class OrderBookLevel:
    """订单簿单个档位"""
    price: float      # 价格（0-1之间）
    size: float       # 数量（shares）
    orders: int = 1   # 订单数（可选，某些API提供）


@dataclass
class OrderBook:
    """市场订单簿"""
    market_id: str
    market_title: str
    timestamp: float

    # 卖单（ask）：从低到高排序（卖一是最低卖出价）
    asks: List[OrderBookLevel]  # asks[0] = 卖一

    # 买单（bid）：从高到低排序（买一是最高买入价）
    bids: List[OrderBookLevel]  # bids[0] = 买一

    # 中间价（用于快速参考）
    mid_price: float = 0.0

    # 最佳买卖价
    best_ask: float = 0.0
    best_bid: float = 0.0

    # 流动性指标
    total_ask_depth: float = 0.0  # 卖单总深度
    total_bid_depth: float = 0.0  # 买单总深度

    @property
    def spread(self) -> float:
        """买卖价差"""
        if self.best_ask > 0 and self.best_bid > 0:
            return self.best_ask - self.best_bid
        return 0.0

    @property
    def spread_bps(self) -> float:
        """买卖价差（基点）"""
        if self.mid_price > 0:
            return (self.spread / self.mid_price) * 10000
        return 0.0


@dataclass
class ExecutableCost:
    """可执行成本计算结果"""
    target_shares: float     # 目标交易数量
    executable_shares: float  # 实际可执行数量
    avg_price: float         # 加权平均价格
    total_cost: float        # 总成本
    slippage: float          # 滑点成本
    is_fully_executable: bool  # 是否可以完全执行
    levels_used: int         # 使用的档位数
    warning: str = ""        # 警告信息（如流动性不足）


@dataclass
class LogicalSpreadPair:
    """逻辑价差事件对（含订单簿成本）"""
    pair_id: str

    # Hard 事件（条件更严格）
    hard_market_id: str
    hard_title: str
    hard_orderbook: Optional[OrderBook]

    # Easy 事件（条件更宽松）
    easy_market_id: str
    easy_title: str
    easy_orderbook: Optional[OrderBook]

    # 逻辑关系
    logical_type: str  # 'price_threshold', 'time_window'
    relationship_desc: str

    # 套利分析
    hard_yes_ask: float = 0.0
    easy_yes_ask: float = 0.0
    spread: float = 0.0

    # 可执行成本分析
    target_position: float = 100.0  # 目标仓位（shares）
    hard_no_cost: Optional[ExecutableCost] = None
    easy_yes_cost: Optional[ExecutableCost] = None
    total_executable_cost: float = 0.0
    total_slippage: float = 0.0

    # 套利收益
    gross_profit: float = 0.0
    net_profit: float = 0.0  # 扣除滑点和手续费

    # 流动性评估
    liquidity_warning: bool = False
    recommendation: str = ""  # 'buy_now', 'place_limit_order', 'wait'


# ============================================================
# 订单簿获取
# ============================================================

class PolymarketOrderBookAPI:
    """
    Polymarket 订单簿 API 客户端

    API 端点：
    - https://gamma-api.polymarket.com/markets - 市场列表（含 bestBid/bestAsk）
    - CLOB 订单簿端点（需要特殊处理）
    """

    BASE_URL = "https://gamma-api.polymarket.com"
    CLOB_BASE_URL = "https://clob.polymarket.com"

    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
        })
        self._cache: Dict[str, Tuple[OrderBook, float]] = {}
        self._cache_ttl = 30  # 缓存30秒

    def get_market_orderbook(self, market_id: str, market_title: str = "",
                             depth: int = 5) -> Optional[OrderBook]:
        """
        获取市场订单簿

        Args:
            market_id: 市场 ID (conditionId)
            market_title: 市场标题
            depth: 获取深度（档位数）

        Returns:
            OrderBook 对象，失败返回 None
        """
        # 检查缓存
        cache_key = f"{market_id}:{depth}"
        if cache_key in self._cache:
            orderbook, cache_time = self._cache[cache_key]
            if time.time() - cache_time < self._cache_ttl:
                return orderbook

        try:
            # 尝试从 CLOB API 获取完整订单簿
            orderbook = self._fetch_clob_orderbook(market_id, market_title, depth)

            if orderbook:
                self._cache[cache_key] = (orderbook, time.time())
                return orderbook

            # 回退：从 markets API 获取 bestBid/bestAsk
            orderbook = self._fetch_market_orderbook(market_id, market_title)

            if orderbook:
                self._cache[cache_key] = (orderbook, time.time())
                return orderbook

            return None

        except Exception as e:
            logger.warning(f"[OrderBook] 获取 {market_id} 失败: {e}")
            return None

    def _fetch_clob_orderbook(self, market_id: str, market_title: str,
                               depth: int) -> Optional[OrderBook]:
        """
        从 CLOB API 获取完整订单簿

        Polymarket CLOB API 端点结构：
        - GET /markets?market_id={conditionId} - 获取市场信息
        - 需要使用 token ID 进行查询
        """
        try:
            # 1. 先获取市场的 token ID
            market_url = f"{self.CLOB_BASE_URL}/markets"
            params = {'condition_id': market_id}

            response = self.session.get(market_url, params=params, timeout=10)
            if response.status_code != 200:
                return None

            data = response.json()
            if not data or len(data) == 0:
                return None

            market_info = data[0]
            token_id = market_info.get('token_id')
            if not token_id:
                return None

            # 2. 获取订单簿
            orderbook_url = f"{self.CLOB_BASE_URL}/orderbook"
            params = {'token_id': token_id}

            response = self.session.get(orderbook_url, params=params, timeout=10)
            if response.status_code != 200:
                return None

            ob_data = response.json()

            # 解析订单簿数据
            asks = []
            bids = []

            # Polymarket CLOB API 返回格式:
            # {"asks": [{"price": "0.55", "size": "100"}, ...],
            #  "bids": [{"price": "0.50", "size": "200"}, ...]}
            raw_asks = ob_data.get('asks', [])
            raw_bids = ob_data.get('bids', [])

            # 支持两种格式：列表 [{price, size}] 或字典 {price: size}
            if isinstance(raw_asks, list):
                for item in raw_asks:
                    asks.append(OrderBookLevel(price=float(item['price']), size=float(item['size'])))
            elif isinstance(raw_asks, dict):
                for price, size in raw_asks.items():
                    asks.append(OrderBookLevel(price=float(price), size=float(size)))

            if isinstance(raw_bids, list):
                for item in raw_bids:
                    bids.append(OrderBookLevel(price=float(item['price']), size=float(item['size'])))
            elif isinstance(raw_bids, dict):
                for price, size in raw_bids.items():
                    bids.append(OrderBookLevel(price=float(price), size=float(size)))

            # 排序
            asks.sort(key=lambda x: x.price)
            bids.sort(key=lambda x: x.price, reverse=True)

            # 限制深度
            asks = asks[:depth]
            bids = bids[:depth]

            if not asks and not bids:
                return None

            # 构建订单簿
            return self._build_orderbook(market_id, market_title, asks, bids)

        except Exception as e:
            logger.debug(f"[CLOB API] {market_id}: {e}")
            return None

    def _fetch_market_orderbook(self, market_id: str, market_title: str) -> Optional[OrderBook]:
        """
        从 markets API 获取简化订单簿（只有 bestBid/bestAsk）

        这是一个回退方案，当 CLOB API 不可用时使用。
        """
        try:
            url = f"{self.BASE_URL}/markets"
            params = {'id': market_id}

            response = self.session.get(url, params=params, timeout=10)
            if response.status_code != 200:
                return None

            data = response.json()
            if not data or len(data) == 0:
                return None

            market = data[0]

            best_bid = market.get('bestBid')
            best_ask = market.get('bestAsk')

            if best_bid is None or best_ask is None:
                # 回退到 outcomePrices
                import json
                outcome_prices_str = market.get('outcomePrices', '[]')
                try:
                    prices = json.loads(outcome_prices_str) if isinstance(outcome_prices_str, str) else outcome_prices_str
                    if prices and len(prices) > 0:
                        yes_price = float(prices[0])
                        if yes_price > 0 and yes_price < 1:
                            best_ask = yes_price
                            best_bid = max(0.01, yes_price - 0.01)
                except (json.JSONDecodeError, ValueError, IndexError) as e:
                    logger.debug(f"[Market API] outcomePrices parse error: {e}")
                    return None

            if best_bid is None or best_ask is None:
                return None

            # 构建单档订单簿
            ask = OrderBookLevel(price=float(best_ask), size=1000.0)  # 假设足够深度
            bid = OrderBookLevel(price=float(best_bid), size=1000.0)

            return self._build_orderbook(market_id, market_title, [ask], [bid])

        except Exception as e:
            logger.debug(f"[Market API] {market_id}: {e}")
            return None

    def _build_orderbook(self, market_id: str, market_title: str,
                        asks: List[OrderBookLevel], bids: List[OrderBookLevel]) -> OrderBook:
        """构建订单簿对象"""
        best_ask = asks[0].price if asks else 0.0
        best_bid = bids[0].price if bids else 0.0
        mid_price = (best_ask + best_bid) / 2 if best_ask > 0 and best_bid > 0 else 0.0

        total_ask_depth = sum(a.size for a in asks)
        total_bid_depth = sum(b.size for b in bids)

        return OrderBook(
            market_id=market_id,
            market_title=market_title,
            timestamp=time.time(),
            asks=asks,
            bids=bids,
            mid_price=mid_price,
            best_ask=best_ask,
            best_bid=best_bid,
            total_ask_depth=total_ask_depth,
            total_bid_depth=total_bid_depth,
        )

    def fetch_multiple_orderbooks(self, market_list: List[Dict],
                                  max_workers: int = 5) -> Dict[str, OrderBook]:
        """
        批量获取多个市场的订单簿

        Args:
            market_list: 市场列表，每个元素包含 {id, title}
            max_workers: 并发线程数

        Returns:
            {market_id: OrderBook} 字典
        """
        results = {}

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='ob_fetch') as executor:
            futures = {
                executor.submit(
                    self.get_market_orderbook,
                    m.get('id', m.get('conditionId', '')),
                    m.get('title', m.get('question', '')),
                    5  # depth
                ): m for m in market_list
            }

            for future in as_completed(futures):
                market = futures[future]
                market_id = market.get('id', market.get('conditionId', ''))
                try:
                    orderbook = future.result(timeout=30)
                    if orderbook:
                        results[market_id] = orderbook
                except Exception as e:
                    logger.warning(f"[OrderBook] 批量获取 {market_id} 失败: {e}")

        logger.info(f"[OrderBook] 批量获取完成: {len(results)}/{len(market_list)}")
        return results


# ============================================================
# 可执行成本计算器
# ============================================================

class ExecutableCostCalculator:
    """可执行成本计算器（保守算法）"""

    @staticmethod
    def calculate_buy_cost(orderbook: OrderBook, target_shares: float,
                          min_price: float = 0.01, max_price: float = 0.99) -> ExecutableCost:
        """
        计算买入成本（保守算法）

        从卖一档开始累加，直到满足目标数量或订单簿耗尽

        Args:
            orderbook: 订单簿
            target_shares: 目标买入数量
            min_price: 最低价格限制
            max_price: 最高价格限制

        Returns:
            ExecutableCost 对象
        """
        if not orderbook or not orderbook.asks:
            return ExecutableCost(
                target_shares=target_shares,
                executable_shares=0.0,
                avg_price=0.0,
                total_cost=0.0,
                slippage=0.0,
                is_fully_executable=False,
                levels_used=0,
                warning="无订单簿数据"
            )

        remaining = target_shares
        total_cost = 0.0
        weighted_sum = 0.0
        levels_used = 0
        warnings = []

        for i, level in enumerate(orderbook.asks):
            if level.price < min_price or level.price > max_price:
                continue

            if remaining <= 0:
                break

            # 当前档位可用数量
            available = level.size
            take = min(remaining, available)

            # 累加成本
            total_cost += take * level.price
            weighted_sum += take * level.price
            remaining -= take
            levels_used += 1

            # 检查是否使用了多档（滑点）
            if levels_used > 1:
                warnings.append(f"使用了{levels_used}档订单")

        executed = target_shares - remaining
        avg_price = weighted_sum / executed if executed > 0 else 0.0

        # 计算滑点（与卖一价比较）
        slippage = 0.0
        if orderbook.best_ask > 0 and executed > 0:
            slippage = ((avg_price - orderbook.best_ask) / orderbook.best_ask) * 100

        # 检查流动性
        is_fully_executable = remaining <= 0
        if not is_fully_executable:
            warnings.append(f"流动性不足：仅可执行 {executed:.1f}/{target_shares:.1f} shares")

        return ExecutableCost(
            target_shares=target_shares,
            executable_shares=executed,
            avg_price=avg_price,
            total_cost=total_cost,
            slippage=slippage,
            is_fully_executable=is_fully_executable,
            levels_used=levels_used,
            warning="; ".join(warnings) if warnings else ""
        )

    @staticmethod
    def calculate_sell_cost(orderbook: OrderBook, target_shares: float,
                           min_price: float = 0.01, max_price: float = 0.99) -> ExecutableCost:
        """
        计算卖出成本（保守算法）

        从买一档开始累加，直到满足目标数量或订单簿耗尽
        注意：卖出获得的是收入，成本是负数

        Args:
            orderbook: 订单簿
            target_shares: 目标卖出数量
            min_price: 最低价格限制
            max_price: 最高价格限制

        Returns:
            ExecutableCost 对象
        """
        if not orderbook or not orderbook.bids:
            return ExecutableCost(
                target_shares=target_shares,
                executable_shares=0.0,
                avg_price=0.0,
                total_cost=0.0,
                slippage=0.0,
                is_fully_executable=False,
                levels_used=0,
                warning="无买单数据"
            )

        remaining = target_shares
        total_income = 0.0
        weighted_sum = 0.0
        levels_used = 0
        warnings = []

        for i, level in enumerate(orderbook.bids):
            if level.price < min_price or level.price > max_price:
                continue

            if remaining <= 0:
                break

            available = level.size
            take = min(remaining, available)

            total_income += take * level.price
            weighted_sum += take * level.price
            remaining -= take
            levels_used += 1

            if levels_used > 1:
                warnings.append(f"使用了{levels_used}档订单")

        executed = target_shares - remaining
        avg_price = weighted_sum / executed if executed > 0 else 0.0

        # 卖出的滑点（与买一价比较）
        slippage = 0.0
        if orderbook.best_bid > 0 and executed > 0:
            slippage = ((orderbook.best_bid - avg_price) / orderbook.best_bid) * 100

        is_fully_executable = remaining <= 0
        if not is_fully_executable:
            warnings.append(f"流动性不足：仅可卖出 {executed:.1f}/{target_shares:.1f} shares")

        # 卖出收入是负成本
        return ExecutableCost(
            target_shares=target_shares,
            executable_shares=executed,
            avg_price=avg_price,
            total_cost=-total_income,  # 负数表示收入
            slippage=slippage,
            is_fully_executable=is_fully_executable,
            levels_used=levels_used,
            warning="; ".join(warnings) if warnings else ""
        )


# ============================================================
# 流动性评估器
# ============================================================

class LiquidityAssessor:
    """流动性评估器"""

    # 流动性等级阈值（shares）
    DEEP_THRESHOLD = 1000      # 深度市场：单档 > 1000 shares
    NORMAL_THRESHOLD = 200     # 正常市场：单档 > 200 shares
    THIN_THRESHOLD = 50        # 薄弱市场：单档 > 50 shares
    # < 50 shares 为流动性枯竭

    @staticmethod
    def assess_orderbook(orderbook: OrderBook) -> Tuple[str, str]:
        """
        评估订单簿流动性

        Returns:
            (level, recommendation) - 流动性等级和交易建议
        """
        if not orderbook:
            return "unknown", "wait"

        best_ask_size = orderbook.asks[0].size if orderbook.asks else 0
        total_ask_depth = orderbook.total_ask_depth

        # 流动性等级
        if best_ask_size >= LiquidityAssessor.DEEP_THRESHOLD:
            level = "deep"
            recommendation = "buy_now"
        elif best_ask_size >= LiquidityAssessor.NORMAL_THRESHOLD:
            level = "normal"
            recommendation = "buy_now"
        elif best_ask_size >= LiquidityAssessor.THIN_THRESHOLD:
            level = "thin"
            recommendation = "place_limit_order"  # 建议挂单
        else:
            level = "depleted"
            recommendation = "wait"  # 建议等待或挂单

        # 检查价差
        if orderbook.spread_bps > 200:  # 价差 > 2%
            level = "_wide_spread"
            if recommendation == "buy_now":
                recommendation = "place_limit_order"

        return level, recommendation

    @staticmethod
    def assess_pair(hard_ob: Optional[OrderBook], easy_ob: Optional[OrderBook],
                   target_shares: float) -> Tuple[bool, str]:
        """
        评估事件对的流动性

        Returns:
            (has_warning, recommendation)
        """
        warnings = []

        # 检查数据可用性
        if not hard_ob:
            warnings.append("Hard 事件无订单簿数据")
        if not easy_ob:
            warnings.append("Easy 事件无订单簿数据")

        if warnings:
            return True, "wait"

        # 评估 Hard 事件流动性（卖出 NO，需要买单深度）
        if hard_ob.bids:
            hard_bid_depth = hard_ob.total_bid_depth
            if hard_bid_depth < target_shares:
                warnings.append(f"Hard 买单深度不足: {hard_bid_depth:.0f} < {target_shares:.0f}")
        else:
            warnings.append("Hard 事件无买单数据")

        # 评估 Easy 事件流动性（买入 YES，需要卖单深度）
        if easy_ob.asks:
            easy_ask_depth = easy_ob.total_ask_depth
            if easy_ask_depth < target_shares:
                warnings.append(f"Easy 卖单深度不足: {easy_ask_depth:.0f} < {target_shares:.0f}")
        else:
            warnings.append("Easy 事件无卖单数据")

        # 综合建议
        if not warnings:
            return False, "buy_now"
        elif len(warnings) == 1 and "depth" in warnings[0].lower():
            return True, "place_limit_order"
        else:
            return True, "wait"


# ============================================================
# 主监控器
# ============================================================

class OrderbookLogicalSpreadMonitor:
    """
    基于订单簿的逻辑价差套利监控器

    功能：
    1. 获取事件对的订单簿数据
    2. 计算真实可执行成本
    3. 评估流动性和给出交易建议
    """

    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.ob_api = PolymarketOrderBookAPI(config)
        self.calculator = ExecutableCostCalculator()
        self.assessor = LiquidityAssessor()
        self.logger = logger

        # 配置参数
        lsa_config = self.config.get('logical_spread_arbitrage', {})
        self.target_position = lsa_config.get('target_position', 100.0)  # 目标仓位（shares）
        self.fee_rate = lsa_config.get('fee_rate', 0.02)
        self.min_spread_threshold = lsa_config.get('min_spread_threshold', 0.0)
        self.max_slippage = lsa_config.get('max_slippage', 5.0)  # 最大可接受滑点

    def analyze_pair(self, pair_info: Dict, orderbooks: Dict[str, OrderBook]) -> Optional[LogicalSpreadPair]:
        """
        分析单个事件对

        Args:
            pair_info: 事件对信息
            orderbooks: {market_id: OrderBook} 字典

        Returns:
            LogicalSpreadPair 对象，分析失败返回 None
        """
        hard_id = pair_info.get('hard_market_id')
        easy_id = pair_info.get('easy_market_id')

        hard_ob = orderbooks.get(hard_id)
        easy_ob = orderbooks.get(easy_id)

        if not hard_ob or not easy_ob:
            return None

        # 获取 YES 价格
        hard_yes = hard_ob.best_ask
        easy_yes = easy_ob.best_ask

        if hard_yes <= 0 or easy_yes <= 0:
            return None

        spread = hard_yes - easy_yes

        # 检查是否满足阈值
        if spread < self.min_spread_threshold:
            return None

        # 计算可执行成本
        # 策略：买入 Hard 的 NO + 买入 Easy 的 YES
        # 买入 Hard NO = 在 YES 订单簿上卖出 YES（使用 bid 侧）
        #   卖出 YES 的收入 = target × avg_bid
        #   NO 的有效成本 = target × (1 - avg_bid) = target - 收入
        # 买入 Easy YES = 在 YES 订单簿上买入 YES（使用 ask 侧）
        hard_sell_yes = self.calculator.calculate_sell_cost(hard_ob, self.target_position)
        easy_yes_cost = self.calculator.calculate_buy_cost(easy_ob, self.target_position)

        # hard_sell_yes.total_cost 为负数（卖出收入），NO 的有效成本 = target + total_cost（负数）
        hard_no_effective_cost = self.target_position + hard_sell_yes.total_cost

        # 总成本 = Hard NO 有效成本 + Easy YES 成本
        total_cost = hard_no_effective_cost + easy_yes_cost.total_cost

        # 计算收益
        if total_cost > 0 and total_cost < self.target_position:
            gross_profit = self.target_position - total_cost
            net_profit = gross_profit - (self.fee_rate * 2 * self.target_position)
        else:
            gross_profit = 0
            net_profit = 0

        # 流动性评估
        liquidity_warning, recommendation = self.assessor.assess_pair(
            hard_ob, easy_ob, self.target_position
        )

        # 总滑点
        total_slippage = abs(hard_sell_yes.slippage) + abs(easy_yes_cost.slippage)

        # 构建结果
        return LogicalSpreadPair(
            pair_id=f"{hard_id}:{easy_id}",
            hard_market_id=hard_id,
            hard_title=pair_info.get('hard_title', ''),
            hard_orderbook=hard_ob,
            easy_market_id=easy_id,
            easy_title=pair_info.get('easy_title', ''),
            easy_orderbook=easy_ob,
            logical_type=pair_info.get('logical_type', 'unknown'),
            relationship_desc=pair_info.get('relationship_desc', ''),
            hard_yes_ask=hard_yes,
            easy_yes_ask=easy_yes,
            spread=spread,
            target_position=self.target_position,
            hard_no_cost=hard_sell_yes,
            easy_yes_cost=easy_yes_cost,
            total_executable_cost=total_cost,
            total_slippage=total_slippage,
            gross_profit=gross_profit,
            net_profit=net_profit,
            liquidity_warning=liquidity_warning,
            recommendation=recommendation,
        )

    def scan_pairs(self, pairs: List[Dict], markets: List[Dict]) -> List[LogicalSpreadPair]:
        """
        扫描事件对列表

        Args:
            pairs: 事件对列表
            markets: 市场列表（用于获取订单簿）

        Returns:
            LogicalSpreadPair 列表
        """
        # 收集所有需要订单簿的市场ID
        market_ids = set()
        for pair in pairs:
            market_ids.add(pair.get('hard_market_id'))
            market_ids.add(pair.get('easy_market_id'))

        # 构建市场列表
        market_list = []
        for m in markets:
            if m.get('id') in market_ids or m.get('conditionId') in market_ids:
                market_list.append(m)

        # 批量获取订单簿
        orderbooks = self.ob_api.fetch_multiple_orderbooks(market_list)

        # 分析每个事件对
        results = []
        for pair in pairs:
            result = self.analyze_pair(pair, orderbooks)
            if result and result.net_profit > 0:
                results.append(result)

        # 按收益排序
        results.sort(key=lambda x: x.net_profit, reverse=True)

        self.logger.info(f"[OrderbookMonitor] 扫描完成: {len(results)} 个套利机会")
        return results


def create_orderbook_monitor(config: Dict = None) -> OrderbookLogicalSpreadMonitor:
    """工厂函数：创建订单簿监控器"""
    return OrderbookLogicalSpreadMonitor(config)
