"""
做市商策略模块 (Market Maker Strategy Module)

移植自 predict-fun-marketmaker 项目的统一做市商策略（Unified Market Maker Strategy）
整合到现有套利监控系统，支持一站式运行。

核心策略：
1. 异步对冲：成交后立即对冲，不撤单
2. 双轨并行：YES + NO 同时挂单
3. 恒定价值：YES + NO = 1，持有 1:1 时风险为零
4. 动态偏移：在第二档挂单，避免被过早成交
5. 积分优化：满足平台积分要求（≤6¢价差，≥100股）
"""

import os
import time
import logging
import threading
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


# ============================================================
# 数据模型
# ============================================================

class MarketMakerState(Enum):
    """做市商状态"""
    STOPPED = 'stopped'       # 已停止
    RUNNING = 'running'       # 运行中
    PAUSED = 'paused'         # 已暂停
    ERROR = 'error'           # 错误


class PositionState(Enum):
    """持仓状态"""
    EMPTY = 'EMPTY'           # 空仓
    HEDGED = 'HEDGED'         # 已对冲（1:1）
    DUAL_TRACK = 'DUAL_TRACK' # 双轨并行


class InventoryLevel(Enum):
    """库存风险等级"""
    SAFE = 'SAFE'             # 安全 (<30%)
    WARNING = 'WARNING'       # 警告 (30-50%)
    DANGER = 'DANGER'         # 危险 (50-70%)
    CRITICAL = 'CRITICAL'     # 危急 (>70%)


@dataclass
class MarketMakerConfig:
    """做市商配置"""
    # 基础配置
    enabled: bool = False
    simulation_mode: bool = True         # 模拟模式（默认开启，不真实下单）
    venue: str = 'predict'               # predict 或 probable

    # API 配置
    api_key: str = ''
    jwt_token: str = ''
    private_key: str = ''
    predict_account_address: str = ''
    api_base_url: str = 'https://api.predict.fun'

    # 做市参数
    spread: float = 0.015                # 基础价差 (1.5%)
    min_spread: float = 0.008            # 最小价差 (0.8%)
    max_spread: float = 0.055            # 最大价差 (5.5%)
    order_size_usd: float = 25.0         # 订单大小（美元）
    max_position_usd: float = 100.0      # 最大持仓（美元）
    max_daily_loss_usd: float = 200.0    # 每日最大亏损（美元）
    max_markets: int = 5                 # 最大市场数量

    # 积分优化
    points_min_shares: int = 100         # 最小订单股数（积分要求）
    points_max_spread_cents: float = 6.0 # 最大价差（美分，积分要求）
    points_optimization: bool = True     # 启用积分优化

    # 对冲配置
    tolerance: float = 0.05              # 对冲偏差容忍度 (5%)
    min_hedge_size: int = 10             # 最小对冲数量
    max_hedge_size: int = 500            # 最大对冲数量
    hedge_slippage_bps: int = 250        # 对冲滑点 (2.5%)

    # 策略模式
    async_hedging: bool = True           # 异步对冲（不撤单直接对冲）
    dual_track_mode: bool = True         # 双轨并行（YES+NO同时挂单）
    dynamic_offset_mode: bool = True     # 动态偏移（第二档挂单）
    buy_offset_bps: int = 100            # Buy 单偏移量（基点，1%）
    sell_offset_bps: int = 100           # Sell 单偏移量（基点，1%）

    # 运行控制
    cycle_interval_ms: int = 3000        # 循环间隔（毫秒）
    max_orders_per_market: int = 2       # 每个市场最大订单数

    # 库存风险阈值
    inventory_safe: float = 0.3          # 安全阈值
    inventory_warning: float = 0.5       # 警告阈值
    inventory_danger: float = 0.7        # 危险阈值


@dataclass
class MMOrder:
    """做市商订单"""
    order_id: str = ''
    market_id: str = ''
    market_title: str = ''
    side: str = ''          # BUY / SELL
    token: str = ''         # YES / NO
    price: float = 0.0
    shares: int = 0
    status: str = 'pending' # pending / open / filled / cancelled
    created_at: float = 0.0
    filled_at: float = 0.0


@dataclass
class MMPosition:
    """做市商持仓"""
    market_id: str = ''
    market_title: str = ''
    yes_shares: int = 0
    no_shares: int = 0
    avg_yes_cost: float = 0.0
    avg_no_cost: float = 0.0
    unrealized_pnl: float = 0.0
    state: str = 'EMPTY'    # EMPTY / HEDGED / DUAL_TRACK

    @property
    def total_shares(self) -> int:
        return self.yes_shares + self.no_shares

    @property
    def deviation(self) -> float:
        """持仓偏差度"""
        avg = self.total_shares / 2 if self.total_shares > 0 else 0
        return abs(self.yes_shares - self.no_shares) / avg if avg > 0 else 0.0

    @property
    def net_value(self) -> float:
        """净值（1:1持仓时最小值=$1/对）"""
        return min(self.yes_shares, self.no_shares)


@dataclass
class MMMarket:
    """做市商市场"""
    market_id: str = ''
    title: str = ''
    yes_price: float = 0.0     # YES mid price
    no_price: float = 0.0      # NO mid price
    yes_bid: float = 0.0       # YES 买一价
    yes_ask: float = 0.0       # YES 卖一价
    no_bid: float = 0.0        # NO 买一价
    no_ask: float = 0.0        # NO 卖一价
    spread_cents: float = 0.0  # 价差（美分）
    volume_24h: float = 0.0    # 24h 交易量
    liquidity: float = 0.0     # 流动性
    points_eligible: bool = False  # 是否满足积分要求
    selected: bool = False     # 是否被选中做市


@dataclass
class MMStats:
    """做市商统计"""
    total_cycles: int = 0
    total_orders: int = 0
    total_fills: int = 0
    total_hedges: int = 0
    total_pnl: float = 0.0
    daily_pnl: float = 0.0
    uptime_seconds: float = 0.0
    start_time: float = 0.0
    last_cycle_time: float = 0.0
    active_orders: int = 0
    active_markets: int = 0
    errors: int = 0
    last_error: str = ''


# ============================================================
# 统一做市商策略
# ============================================================

class UnifiedMarketMakerStrategy:
    """
    统一做市商策略（移植自 TypeScript 版本）

    工作流程：
    - 初始：挂 YES Buy + NO Buy（第二档）
    - 被成交：立刻对冲（买入对面）→ 1:1 持仓
    - 双轨并行：继续挂 Buy 单 + 挂 Sell 单
    - 持续循环：积分收益最大化
    """

    def __init__(self, config: MarketMakerConfig):
        self.config = config

    def analyze_position(self, position: MMPosition, market: MMMarket) -> dict:
        """分析持仓状态，给出操作建议"""
        yes_shares = position.yes_shares
        no_shares = position.no_shares
        total = yes_shares + no_shares

        # 计算偏差
        avg = total / 2 if total > 0 else 0
        deviation = abs(yes_shares - no_shares) / avg if avg > 0 else 0.0
        is_balanced = deviation <= self.config.tolerance

        # 判断状态
        should_buy = False
        should_sell = False

        if total == 0:
            state = PositionState.EMPTY
            should_buy = True
        elif is_balanced and total >= self.config.min_hedge_size:
            state = PositionState.DUAL_TRACK
            should_buy = True
            should_sell = True
        elif not is_balanced:
            state = PositionState.HEDGED
            should_buy = True
            should_sell = yes_shares > 0 or no_shares > 0
        else:
            state = PositionState.EMPTY
            should_buy = True

        # 计算订单大小
        base_size = max(10, self.config.min_hedge_size)
        buy_size = base_size if should_buy else 0
        sell_size = min(base_size, total // 2) if should_sell else 0

        return {
            'state': state.value,
            'deviation': round(deviation * 100, 2),
            'should_buy': should_buy,
            'should_sell': should_sell,
            'buy_size': buy_size,
            'sell_size': sell_size,
        }

    def get_inventory_level(self, position: MMPosition) -> InventoryLevel:
        """计算库存风险等级"""
        total = position.total_shares
        if total == 0:
            return InventoryLevel.SAFE

        ratio = abs(position.yes_shares - position.no_shares) / total
        if ratio >= self.config.inventory_danger:
            return InventoryLevel.CRITICAL
        elif ratio >= self.config.inventory_warning:
            return InventoryLevel.DANGER
        elif ratio >= self.config.inventory_safe:
            return InventoryLevel.WARNING
        return InventoryLevel.SAFE

    def calculate_spread_adjustment(self, inventory_level: InventoryLevel) -> Tuple[float, float]:
        """根据库存风险调整价差和订单大小倍数"""
        adjustments = {
            InventoryLevel.SAFE: (1.0, 1.0),       # 正常
            InventoryLevel.WARNING: (1.2, 0.8),     # 1.2x价差, 0.8x订单
            InventoryLevel.DANGER: (1.5, 0.5),      # 1.5x价差, 0.5x订单
            InventoryLevel.CRITICAL: (2.0, 0.0),    # 2x价差, 暂停挂单
        }
        return adjustments.get(inventory_level, (1.0, 1.0))

    def calculate_order_prices(self, market: MMMarket) -> dict:
        """
        计算挂单价格

        动态偏移模式：根据第一档价格偏移，避免成为第一档
        固定价差模式：基于中间价的固定百分比价差
        """
        if self.config.dynamic_offset_mode:
            buy_offset = self.config.buy_offset_bps / 10000
            sell_offset = self.config.sell_offset_bps / 10000

            # YES 挂单价格
            yes_bid = max(0.01, market.yes_bid * (1 - buy_offset))
            yes_ask = min(0.99, market.yes_ask * (1 + sell_offset))

            # NO 挂单价格
            no_bid = max(0.01, market.no_bid * (1 - buy_offset))
            no_ask = min(0.99, market.no_ask * (1 + sell_offset))

            source = 'DYNAMIC_OFFSET'
        else:
            spread = self.config.spread
            yes_bid = max(0.01, market.yes_price * (1 - spread))
            yes_ask = min(0.99, market.yes_price * (1 + spread))
            no_bid = max(0.01, market.no_price * (1 - spread))
            no_ask = min(0.99, market.no_price * (1 + spread))
            source = 'FIXED_SPREAD'

        return {
            'yes_bid': round(yes_bid, 4),
            'yes_ask': round(yes_ask, 4),
            'no_bid': round(no_bid, 4),
            'no_ask': round(no_ask, 4),
            'source': source,
            'spread_cents': round(abs(yes_ask - yes_bid) * 100, 2),
        }

    def check_hedge_needed(self, position: MMPosition, filled_side: str,
                           filled_token: str, filled_shares: int) -> Optional[dict]:
        """
        检查是否需要对冲

        异步对冲逻辑：成交后不撤单，直接执行对冲
        """
        yes = position.yes_shares
        no = position.no_shares

        # 模拟成交后的持仓
        if filled_side == 'BUY':
            if filled_token == 'YES':
                yes += filled_shares
            else:
                no += filled_shares
        else:
            if filled_token == 'YES':
                yes -= filled_shares
            else:
                no -= filled_shares

        total = yes + no
        avg = total / 2 if total > 0 else 0
        deviation = abs(yes - no) / avg if avg > 0 else 0.0

        if deviation > self.config.tolerance and total >= self.config.min_hedge_size:
            if yes > no:
                hedge_shares = min(yes - no, self.config.max_hedge_size)
                return {
                    'action': 'BUY_NO',
                    'shares': hedge_shares,
                    'reason': f'异步对冲：YES过多({yes}>{no})，买入 {hedge_shares} NO',
                    'priority': 'URGENT',
                }
            else:
                hedge_shares = min(no - yes, self.config.max_hedge_size)
                return {
                    'action': 'BUY_YES',
                    'shares': hedge_shares,
                    'reason': f'异步对冲：NO过多({no}>{yes})，买入 {hedge_shares} YES',
                    'priority': 'URGENT',
                }
        return None

    def score_market(self, market: MMMarket) -> float:
        """评估市场做市适合度（0-100）"""
        score = 0.0

        # 价差评分（越小越好，但不能太小）
        spread = market.spread_cents
        if 1.0 <= spread <= 3.0:
            score += 30
        elif 3.0 < spread <= 6.0:
            score += 20
        elif spread < 1.0:
            score += 10  # 太小，利润空间小
        else:
            score += 5

        # 流动性评分
        if market.liquidity >= 10000:
            score += 25
        elif market.liquidity >= 5000:
            score += 20
        elif market.liquidity >= 1000:
            score += 15
        else:
            score += 5

        # 交易量评分
        if market.volume_24h >= 50000:
            score += 25
        elif market.volume_24h >= 10000:
            score += 20
        elif market.volume_24h >= 5000:
            score += 15
        else:
            score += 5

        # 积分加成
        if market.points_eligible:
            score += 20

        return min(100, score)


# ============================================================
# 做市商引擎
# ============================================================

class MarketMakerEngine:
    """
    做市商引擎

    管理做市商的完整生命周期：
    - 市场扫描与选择
    - 策略计算
    - 模拟/真实下单
    - 持仓管理
    - 对冲执行
    """

    def __init__(self, config: Optional[MarketMakerConfig] = None):
        self.config = config or MarketMakerConfig()
        self.strategy = UnifiedMarketMakerStrategy(self.config)

        # 状态
        self._state = MarketMakerState.STOPPED
        self._lock = threading.Lock()
        self._running = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # 数据
        self.markets: Dict[str, MMMarket] = {}
        self.positions: Dict[str, MMPosition] = {}
        self.orders: List[MMOrder] = []
        self.order_history: List[MMOrder] = []
        self.stats = MMStats()
        self.log_entries: List[dict] = []

        # 使用现有的 Predict.fun API 客户端
        self._api_client = None

    def load_config_from_env(self):
        """从环境变量加载配置"""
        self.config.enabled = os.getenv('MM_ENABLED', 'false').lower() == 'true'
        self.config.simulation_mode = os.getenv('MM_SIMULATION_MODE', 'true').lower() == 'true'
        self.config.venue = os.getenv('MM_VENUE', 'predict')
        self.config.api_key = os.getenv('API_KEY', os.getenv('PREDICT_API_KEY', ''))
        self.config.jwt_token = os.getenv('JWT_TOKEN', '')
        self.config.private_key = os.getenv('PRIVATE_KEY', '')
        self.config.predict_account_address = os.getenv('PREDICT_ACCOUNT_ADDRESS', '')
        self.config.api_base_url = os.getenv('API_BASE_URL', 'https://api.predict.fun')
        self.config.spread = float(os.getenv('SPREAD', '0.015'))
        self.config.min_spread = float(os.getenv('MIN_SPREAD', '0.008'))
        self.config.max_spread = float(os.getenv('MAX_SPREAD', '0.055'))
        self.config.order_size_usd = float(os.getenv('ORDER_SIZE', '25'))
        self.config.max_position_usd = float(os.getenv('MAX_POSITION', '100'))
        self.config.max_daily_loss_usd = float(os.getenv('MAX_DAILY_LOSS', '200'))
        self.config.max_markets = int(os.getenv('MAX_MARKETS', '5'))
        self.config.points_min_shares = int(os.getenv('MM_POINTS_MIN_SHARES', '100'))
        self.config.points_max_spread_cents = float(os.getenv('MM_POINTS_MAX_SPREAD_CENTS', '6'))
        self.config.points_optimization = os.getenv('MM_POINTS_OPTIMIZATION', 'true').lower() == 'true'
        self.config.cycle_interval_ms = int(os.getenv('MIN_ORDER_INTERVAL_MS', '3000'))

        logger.info(f"做市商配置加载完成: venue={self.config.venue}, "
                    f"simulation={self.config.simulation_mode}, "
                    f"spread={self.config.spread}")

    def update_config(self, updates: dict):
        """从前端更新配置"""
        with self._lock:
            for key, value in updates.items():
                if hasattr(self.config, key):
                    setattr(self.config, key, value)
            # 重建策略实例
            self.strategy = UnifiedMarketMakerStrategy(self.config)
            self._add_log('CONFIG', f'配置已更新: {list(updates.keys())}')

    def get_state(self) -> dict:
        """获取完整状态（用于前端渲染）"""
        with self._lock:
            # 计算运行时间
            uptime = time.time() - self.stats.start_time if self.stats.start_time > 0 else 0

            return {
                'status': self._state.value,
                'config': {
                    'enabled': self.config.enabled,
                    'simulation_mode': self.config.simulation_mode,
                    'venue': self.config.venue,
                    'spread': self.config.spread,
                    'order_size_usd': self.config.order_size_usd,
                    'max_position_usd': self.config.max_position_usd,
                    'max_daily_loss_usd': self.config.max_daily_loss_usd,
                    'max_markets': self.config.max_markets,
                    'points_optimization': self.config.points_optimization,
                    'points_min_shares': self.config.points_min_shares,
                    'points_max_spread_cents': self.config.points_max_spread_cents,
                    'dual_track_mode': self.config.dual_track_mode,
                    'dynamic_offset_mode': self.config.dynamic_offset_mode,
                    'async_hedging': self.config.async_hedging,
                    'cycle_interval_ms': self.config.cycle_interval_ms,
                    'has_api_key': bool(self.config.api_key),
                    'has_jwt': bool(self.config.jwt_token),
                },
                'stats': {
                    'total_cycles': self.stats.total_cycles,
                    'total_orders': self.stats.total_orders,
                    'total_fills': self.stats.total_fills,
                    'total_hedges': self.stats.total_hedges,
                    'daily_pnl': round(self.stats.daily_pnl, 4),
                    'total_pnl': round(self.stats.total_pnl, 4),
                    'uptime': round(uptime, 0),
                    'active_orders': self.stats.active_orders,
                    'active_markets': len([m for m in self.markets.values() if m.selected]),
                    'errors': self.stats.errors,
                    'last_error': self.stats.last_error,
                },
                'markets': [self._market_to_dict(m) for m in self.markets.values()],
                'positions': [self._position_to_dict(p) for p in self.positions.values()],
                'orders': [self._order_to_dict(o) for o in self.orders[-20:]],
                'logs': self.log_entries[-30:],
            }

    def start(self):
        """启动做市商"""
        with self._lock:
            if self._state == MarketMakerState.RUNNING:
                return

            self._state = MarketMakerState.RUNNING
            self._running.set()
            self.stats.start_time = time.time()
            self._add_log('SYSTEM', f'做市商启动 ({"模拟" if self.config.simulation_mode else "实盘"}模式)')

            self._thread = threading.Thread(target=self._run_loop, daemon=True, name='mm-engine')
            self._thread.start()

    def stop(self):
        """停止做市商"""
        with self._lock:
            if self._state == MarketMakerState.STOPPED:
                return
            self._running.clear()
            self._state = MarketMakerState.STOPPED
            self._add_log('SYSTEM', '做市商已停止')

    def pause(self):
        """暂停做市商"""
        with self._lock:
            if self._state == MarketMakerState.RUNNING:
                self._state = MarketMakerState.PAUSED
                self._add_log('SYSTEM', '做市商已暂停')

    def resume(self):
        """恢复做市商"""
        with self._lock:
            if self._state == MarketMakerState.PAUSED:
                self._state = MarketMakerState.RUNNING
                self._add_log('SYSTEM', '做市商已恢复')

    def select_markets(self, market_ids: List[str]):
        """手动选择做市市场"""
        with self._lock:
            for m in self.markets.values():
                m.selected = m.market_id in market_ids
            selected = [m.title for m in self.markets.values() if m.selected]
            self._add_log('MARKET', f'已选择 {len(selected)} 个市场')

    def recommend_markets(self) -> List[dict]:
        """推荐做市市场（按评分排序）"""
        with self._lock:
            scored = []
            for m in self.markets.values():
                score = self.strategy.score_market(m)
                scored.append({
                    'market_id': m.market_id,
                    'title': m.title,
                    'score': round(score, 1),
                    'spread_cents': m.spread_cents,
                    'volume_24h': m.volume_24h,
                    'liquidity': m.liquidity,
                    'points_eligible': m.points_eligible,
                    'yes_price': m.yes_price,
                    'no_price': m.no_price,
                })
            scored.sort(key=lambda x: x['score'], reverse=True)
            return scored[:20]

    # ============================================================
    # 内部方法
    # ============================================================

    def _run_loop(self):
        """做市商主循环"""
        logger.info("做市商引擎主循环启动")
        while self._running.is_set():
            try:
                if self._state == MarketMakerState.PAUSED:
                    time.sleep(1)
                    continue

                cycle_start = time.time()
                self._run_cycle()
                self.stats.total_cycles += 1
                self.stats.last_cycle_time = time.time()

                # 等待下一个周期
                elapsed_ms = (time.time() - cycle_start) * 1000
                sleep_ms = max(500, self.config.cycle_interval_ms - elapsed_ms)
                time.sleep(sleep_ms / 1000)

            except Exception as e:
                logger.error(f"做市商循环异常: {e}", exc_info=True)
                self.stats.errors += 1
                self.stats.last_error = str(e)
                self._add_log('ERROR', f'循环异常: {e}')
                time.sleep(5)

        logger.info("做市商引擎主循环结束")

    def _run_cycle(self):
        """执行一个做市周期"""
        with self._lock:
            # 1. 刷新市场数据
            self._refresh_markets()

            # 2. 对每个选中的市场执行策略
            selected_markets = [m for m in self.markets.values() if m.selected]
            for market in selected_markets:
                self._process_market(market)

            # 3. 更新统计
            self.stats.active_orders = len([o for o in self.orders if o.status == 'open'])
            self.stats.active_markets = len(selected_markets)

    def _refresh_markets(self):
        """刷新市场数据（使用现有 API 客户端）"""
        try:
            if self._api_client is None:
                self._init_api_client()

            if self._api_client is None:
                return

            raw_markets = self._api_client.get_markets(status='open', limit=100)
            for raw in raw_markets:
                mid = str(raw.get('id', raw.get('marketId', raw.get('market_id', ''))))
                if not mid:
                    continue

                title = raw.get('title', raw.get('question', ''))
                yes_price = self._extract_price(raw, 'yes')
                no_price = self._extract_price(raw, 'no')
                yes_bid = float(raw.get('yes_bid', raw.get('bestBid', yes_price * 0.99)))
                yes_ask = float(raw.get('yes_ask', raw.get('bestAsk', yes_price * 1.01)))
                no_bid = max(0.01, 1.0 - yes_ask)
                no_ask = min(0.99, 1.0 - yes_bid)
                spread = abs(yes_ask - yes_bid) * 100
                volume = float(raw.get('volume_24h', raw.get('volume24hr', raw.get('volume', 0))))
                liquidity = float(raw.get('liquidity', raw.get('liquidityClob', 0)))

                # 积分资格检查
                points_eligible = (
                    spread <= self.config.points_max_spread_cents
                    and liquidity >= 100
                )

                if mid not in self.markets:
                    self.markets[mid] = MMMarket()

                m = self.markets[mid]
                m.market_id = mid
                m.title = title
                m.yes_price = yes_price
                m.no_price = no_price
                m.yes_bid = yes_bid
                m.yes_ask = yes_ask
                m.no_bid = no_bid
                m.no_ask = no_ask
                m.spread_cents = round(spread, 2)
                m.volume_24h = volume
                m.liquidity = liquidity
                m.points_eligible = points_eligible

        except Exception as e:
            logger.warning(f"刷新市场数据失败: {e}")
            self._add_log('WARNING', f'市场数据刷新失败: {e}')

    def _init_api_client(self):
        """初始化 API 客户端（复用现有模块）"""
        try:
            from src.api_client import PredictAPIClient
            api_config = {
                'api': {
                    'api_key': self.config.api_key,
                    'base_url': self.config.api_base_url,
                }
            }
            self._api_client = PredictAPIClient(api_config)
            self._add_log('SYSTEM', f'API 客户端初始化成功: {self.config.api_base_url}')
        except Exception as e:
            logger.warning(f"API 客户端初始化失败: {e}")
            self._add_log('ERROR', f'API 客户端初始化失败: {e}')

    def _extract_price(self, raw: dict, side: str) -> float:
        """从原始市场数据中提取价格"""
        if side == 'yes':
            # 尝试多种字段名
            for key in ['yesPrice', 'yes_price', 'current_price', 'lastPrice', 'bestAsk']:
                val = raw.get(key)
                if val is not None:
                    return float(val)
            # 从 outcomePrices 提取
            prices = raw.get('outcomePrices')
            if prices:
                import json
                if isinstance(prices, str):
                    prices = json.loads(prices)
                if isinstance(prices, list) and len(prices) > 0:
                    return float(prices[0])
            return 0.5
        else:
            yes = self._extract_price(raw, 'yes')
            return max(0.01, 1.0 - yes)

    def _process_market(self, market: MMMarket):
        """处理单个市场的做市策略"""
        # 获取或创建持仓
        if market.market_id not in self.positions:
            self.positions[market.market_id] = MMPosition(
                market_id=market.market_id,
                market_title=market.title,
            )
        position = self.positions[market.market_id]

        # 1. 分析持仓状态
        analysis = self.strategy.analyze_position(position, market)
        position.state = analysis['state']

        # 2. 检查库存风险
        inv_level = self.strategy.get_inventory_level(position)
        spread_mult, size_mult = self.strategy.calculate_spread_adjustment(inv_level)

        if inv_level == InventoryLevel.CRITICAL:
            self._add_log('RISK', f'{market.title}: 库存危急，暂停挂单')
            return

        # 3. 计算挂单价格
        prices = self.strategy.calculate_order_prices(market)

        # 4. 模拟/真实下单
        if analysis['should_buy'] and analysis['buy_size'] > 0:
            shares = max(self.config.points_min_shares, int(analysis['buy_size'] * size_mult))
            self._place_order(market, 'BUY', 'YES', prices['yes_bid'], shares)
            if self.config.dual_track_mode:
                self._place_order(market, 'BUY', 'NO', prices['no_bid'], shares)

        if analysis['should_sell'] and analysis['sell_size'] > 0:
            shares = int(analysis['sell_size'] * size_mult)
            if shares > 0:
                self._place_order(market, 'SELL', 'YES', prices['yes_ask'], shares)
                if self.config.dual_track_mode:
                    self._place_order(market, 'SELL', 'NO', prices['no_ask'], shares)

    def _place_order(self, market: MMMarket, side: str, token: str,
                     price: float, shares: int):
        """下单（模拟模式记录，实盘模式执行）"""
        order = MMOrder(
            order_id=f"sim_{int(time.time()*1000)}_{side}_{token}",
            market_id=market.market_id,
            market_title=market.title,
            side=side,
            token=token,
            price=round(price, 4),
            shares=shares,
            status='open' if self.config.simulation_mode else 'pending',
            created_at=time.time(),
        )

        self.orders.append(order)
        self.stats.total_orders += 1

        if self.config.simulation_mode:
            # 模拟模式：模拟成交逻辑
            self._simulate_fill(order, market)
        else:
            # 实盘模式：通过 API 下单
            self._execute_order(order)

        # 限制订单列表大小
        if len(self.orders) > 100:
            self.orders = self.orders[-50:]

    def _simulate_fill(self, order: MMOrder, market: MMMarket):
        """模拟成交"""
        import random
        # 模拟成交概率（基于价格偏离度）
        if order.side == 'BUY':
            ref_price = market.yes_price if order.token == 'YES' else market.no_price
            fill_prob = 0.3 if order.price >= ref_price * 0.98 else 0.1
        else:
            ref_price = market.yes_price if order.token == 'YES' else market.no_price
            fill_prob = 0.3 if order.price <= ref_price * 1.02 else 0.1

        if random.random() < fill_prob:
            order.status = 'filled'
            order.filled_at = time.time()
            self.stats.total_fills += 1

            # 更新持仓
            pos = self.positions.get(order.market_id)
            if pos:
                if order.side == 'BUY':
                    if order.token == 'YES':
                        pos.yes_shares += order.shares
                    else:
                        pos.no_shares += order.shares
                else:
                    if order.token == 'YES':
                        pos.yes_shares = max(0, pos.yes_shares - order.shares)
                    else:
                        pos.no_shares = max(0, pos.no_shares - order.shares)

                # 检查是否需要对冲
                if self.config.async_hedging:
                    hedge = self.strategy.check_hedge_needed(
                        pos, order.side, order.token, order.shares
                    )
                    if hedge:
                        self.stats.total_hedges += 1
                        self._add_log('HEDGE', hedge['reason'])

            self._add_log('FILL',
                          f'{order.token} {order.side} {order.shares}股 @ {order.price:.4f} '
                          f'({market.title[:30]})')

    def _execute_order(self, order: MMOrder):
        """实盘下单（预留接口）"""
        # TODO: 对接真实 API 下单
        self._add_log('ORDER', f'[实盘] {order.token} {order.side} {order.shares}股 @ {order.price:.4f}')
        order.status = 'pending'

    def _add_log(self, level: str, message: str):
        """添加日志条目"""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=8)))
        entry = {
            'time': now.strftime('%H:%M:%S'),
            'level': level,
            'message': message,
        }
        self.log_entries.append(entry)
        if len(self.log_entries) > 200:
            self.log_entries = self.log_entries[-100:]

        # 也输出到标准日志
        log_level = {
            'ERROR': logging.ERROR,
            'WARNING': logging.WARNING,
            'RISK': logging.WARNING,
        }.get(level, logging.INFO)
        logger.log(log_level, f"[MM] {message}")

    def _market_to_dict(self, m: MMMarket) -> dict:
        return {
            'market_id': m.market_id,
            'title': m.title,
            'yes_price': m.yes_price,
            'no_price': m.no_price,
            'yes_bid': m.yes_bid,
            'yes_ask': m.yes_ask,
            'spread_cents': m.spread_cents,
            'volume_24h': m.volume_24h,
            'liquidity': m.liquidity,
            'points_eligible': m.points_eligible,
            'selected': m.selected,
        }

    def _position_to_dict(self, p: MMPosition) -> dict:
        return {
            'market_id': p.market_id,
            'market_title': p.market_title,
            'yes_shares': p.yes_shares,
            'no_shares': p.no_shares,
            'total_shares': p.total_shares,
            'deviation': round(p.deviation * 100, 2),
            'net_value': p.net_value,
            'state': p.state,
        }

    def _order_to_dict(self, o: MMOrder) -> dict:
        return {
            'order_id': o.order_id,
            'market_title': o.market_title,
            'side': o.side,
            'token': o.token,
            'price': o.price,
            'shares': o.shares,
            'status': o.status,
            'time': time.strftime('%H:%M:%S', time.localtime(o.created_at)) if o.created_at else '',
        }


# ============================================================
# 全局单例
# ============================================================
_engine: Optional[MarketMakerEngine] = None


def get_market_maker_engine() -> MarketMakerEngine:
    """获取做市商引擎全局单例"""
    global _engine
    if _engine is None:
        _engine = MarketMakerEngine()
        _engine.load_config_from_env()
    return _engine
