"""
Polymarket API 客户端模块
支持真实的 CLOB API 和模拟模式
增强版：支持热门市场筛选、精确价格获取
"""

import time
import random
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
from datetime import datetime

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.constants import POLYGON
    PY_CLOB_AVAILABLE = True
except ImportError:
    PY_CLOB_AVAILABLE = False
    # 定义默认值（如果未安装 py-clob-client）
    POLYGON = 137  # Polygon chain ID


logger = logging.getLogger(__name__)


@dataclass
class PolymarketMarket:
    """Polymarket 市场信息"""
    question_id: str
    question_title: str
    current_price: float
    volume_24h: float
    end_date: Optional[str] = None
    condition_id: str = ""


@dataclass
class PolymarketOrderBook:
    """Polymarket 订单簿"""
    yes_bid: float
    yes_ask: float
    yes_bid_size: float = 100.0
    yes_ask_size: float = 100.0


class MarketSortBy(Enum):
    """市场排序方式"""
    VOLUME = "volume"
    NEWEST = "newest"
    CLOSING_SOON = "closing_soon"
    PRICE_HIGH = "price_high"
    PRICE_LOW = "price_low"


class RealPolymarketClient:
    """
    真实的 Polymarket API 客户端
    使用官方 py-clob-client 库和 Gamma API
    API 文档: https://docs.polymarket.com/developers/CLOB/introduction
    """

    def __init__(self, config: Dict):
        self.config = config
        self.logger = logging.getLogger(__name__)

        # Gamma API 端点（用于获取市场数据）
        self.gamma_host = "gamma-api.polymarket.com"
        # CLOB API 端点（用于交易）
        self.host = "clob.polymarket.com"
        self.chain_id = POLYGON  # Polygon 网络

        # 初始化客户端（不需要私钥来读取市场数据）
        if PY_CLOB_AVAILABLE:
            try:
                # 创建不需要签名的客户端（只读取数据）
                self.client = ClobClient(
                    host=self.host,
                    chain_id=self.chain_id,
                    signature_type=1,  # 不使用签名
                    key=None,          # 不需要私钥来读取数据
                )
                self.logger.info("Polymarket CLOB 客户端初始化成功")
            except Exception as e:
                self.logger.warning(f"CLOB 客户端初始化失败，将使用 HTTP API: {e}")
                self.client = None
        else:
            self.logger.warning("py-clob-client 未安装，请运行: pip install py-clob-client")
            self.client = None

        # 使用 requests 作为备用
        import requests
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Origin': 'https://polymarket.com',
            'Referer': 'https://polymarket.com/'
        })
        self.base_url = f"https://{self.gamma_host}"  # 使用 Gamma API

        # 缓存
        self._markets_cache: List[Dict] = []
        self._market_info_cache: Dict[str, Dict] = {}
        self._cache_time = 0
        self._cache_duration = config.get('polymarket', {}).get('cache_seconds', 30)

        # 价格缓存
        self._price_cache: Dict[str, Tuple[float, float]] = {}  # {token_id: (price, timestamp)}
        self._price_cache_duration = 10  # 价格缓存 10 秒

    def get_all_markets(self, limit: int = 1000, active_only: bool = True) -> List[Dict]:
        """
        获取所有市场列表（使用公开 API）
        优先获取当前活跃的市场，而不是过期市场

        Args:
            limit: 返回数量限制（最大1000）
            active_only: 是否只返回活跃市场

        API 文档: https://docs.polymarket.com/quickstart/fetching-data
        """
        try:
            # 检查缓存
            if time.time() - self._cache_time < self._cache_duration and self._markets_cache:
                self.logger.debug(f"使用缓存的市场列表 ({len(self._markets_cache)} 个)")
                markets = self._markets_cache
            else:
                # 使用公开的 Gamma API
                # 不使用 active 参数，因为该参数可能导致连接问题
                # 而是获取更多市场后手动过滤
                params = {'limit': min(limit, 1000)}

                response = self.session.get(
                    f"{self.base_url}/markets",
                    params=params,
                    timeout=15
                )
                response.raise_for_status()
                markets = response.json()

                # 手动过滤：只返回未关闭且有效期的市场
                if active_only:
                    current_time = time.time()
                    # 过滤掉已关闭的市场或已过期的市场
                    filtered_markets = []
                    for market in markets:
                        # 检查是否已关闭
                        if market.get('closed', False):
                            continue
                        # 检查是否已过期（如果有结束日期）
                        end_date = market.get('end_date')
                        if end_date:
                            try:
                                from datetime import datetime
                                if isinstance(end_date, str):
                                    end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                                    # 如果已过期，跳过
                                    if end_dt.timestamp() < current_time:
                                        continue
                            except:
                                pass  # 日期解析失败，保留该市场
                        filtered_markets.append(market)

                    markets = filtered_markets

                # 更新缓存
                self._markets_cache = markets
                self._cache_time = time.time()

            self.logger.info(f"获取到 {len(markets)} 个市场")
            return markets[:limit]

        except Exception as e:
            self.logger.error(f"获取市场列表失败: {e}")
            return self._markets_cache if self._markets_cache else []

    def get_popular_markets(self, limit: int = 20, min_volume: float = 1000) -> List[Dict]:
        """
        获取热门市场（按成交量排序）

        Args:
            limit: 返回数量
            min_volume: 最小成交量阈值（美元）
        """
        try:
            markets = self.get_all_markets(limit=200, active_only=True)

            # 过滤并排序
            filtered_markets = []
            for market in markets:
                volume = market.get('volume', 0)

                # 处理不同格式的成交量
                if isinstance(volume, (int, float)):
                    volume_usd = float(volume)
                elif isinstance(volume, str):
                    try:
                        volume_usd = float(volume)
                    except:
                        volume_usd = 0
                else:
                    volume_usd = 0

                # 只保留高成交量市场
                if volume_usd >= min_volume:
                    market['volume_usd'] = volume_usd
                    filtered_markets.append(market)

            # 按成交量降序排序
            filtered_markets.sort(key=lambda m: m.get('volume_usd', 0), reverse=True)

            result = filtered_markets[:limit]
            self.logger.info(f"获取到 {len(result)} 个热门市场（最小成交量: ${min_volume}）")
            return result

        except Exception as e:
            self.logger.error(f"获取热门市场失败: {e}")
            return []

    def get_markets_by_tag(self, tag: str, limit: int = 20) -> List[Dict]:
        """
        按标签获取市场

        Args:
            tag: 标签名称（如 "politics", "crypto", "sports"）
            limit: 返回数量
        """
        try:
            markets = self.get_all_markets(limit=200, active_only=True)

            # 过滤包含指定标签的市场
            filtered = []
            tag_lower = tag.lower()

            for market in markets:
                tags = market.get('tags', [])
                if not isinstance(tags, list):
                    tags = []

                if any(tag_lower in str(t).lower() for t in tags):
                    filtered.append(market)

            self.logger.info(f"标签 '{tag}': 找到 {len(filtered)} 个市场")
            return filtered[:limit]

        except Exception as e:
            self.logger.error(f"按标签获取市场失败: {e}")
            return []

    def get_markets_closing_soon(self, hours: int = 24, limit: int = 20) -> List[Dict]:
        """
        获取即将到期的市场

        Args:
            hours: 多少小时内到期
            limit: 返回数量
        """
        try:
            markets = self.get_all_markets(limit=200, active_only=True)

            # 过滤即将到期的市场
            from datetime import datetime, timedelta

            now = datetime.utcnow()
            deadline = now + timedelta(hours=hours)

            closing_soon = []
            for market in markets:
                end_date = market.get('end_date')
                if not end_date:
                    continue

                try:
                    # 解析日期（ISO 8601 格式）
                    end_datetime = datetime.fromisoformat(end_date.replace('Z', '+00:00'))

                    if end_datetime <= deadline:
                        market['hours_until_close'] = (end_datetime - now).total_seconds() / 3600
                        closing_soon.append(market)

                except (ValueError, TypeError):
                    continue

            # 按到期时间排序
            closing_soon.sort(key=lambda m: m.get('hours_until_close', float('inf')))

            self.logger.info(f"找到 {len(closing_soon)} 个即将在 {hours} 小时内到期的市场")
            return closing_soon[:limit]

        except Exception as e:
            self.logger.error(f"获取即将到期市场失败: {e}")
            return []

    def get_market_price(self, token_id: str, side: str = "BUY") -> Optional[float]:
        """
        获取指定 token 的精确价格

        Args:
            token_id: Token ID (condition_id)
            side: "BUY" 或 "SELL"
        """
        try:
            # 检查价格缓存
            cache_key = f"{token_id}_{side}"
            if cache_key in self._price_cache:
                cached_price, cached_time = self._price_cache[cache_key]
                if time.time() - cached_time < self._price_cache_duration:
                    return cached_price

            # 使用价格 API 获取最新价格
            response = self.session.get(
                f"{self.base_url}/price",
                params={'token_id': token_id, 'side': side},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            # Polymarket 返回的价格格式
            price = data.get('price', 0)

            # 转换为标准格式
            if isinstance(price, (int, float)):
                # 价格可能是小数（如 0.65）或大数（如 6500，需要除以 10000）
                if price > 1:
                    price = price / 10000
                price = float(price)
            elif isinstance(price, str):
                price = float(price)
            else:
                price = 0.5

            # 限制在合理范围内
            price = round(max(0.01, min(0.99, price)), 4)

            # 更新缓存
            self._price_cache[cache_key] = (price, time.time())

            return price

        except Exception as e:
            self.logger.debug(f"获取价格失败 {token_id}: {e}")
            return None

    def get_token_prices(self, condition_id: str) -> Dict[str, float]:
        """
        获取一个市场的所有 token 价格（Yes 和 No）

        Args:
            condition_id: 市场 ID

        Returns:
            {'yes': 0.65, 'no': 0.35}
        """
        prices = {'yes': 0.5, 'no': 0.5}

        try:
            # 获取 Yes 价格
            yes_price = self.get_market_price(condition_id, "BUY")
            if yes_price:
                prices['yes'] = yes_price
                prices['no'] = round(1 - yes_price, 4)

        except Exception as e:
            self.logger.debug(f"获取 token 价格失败 {condition_id}: {e}")

        return prices

    def get_market_info(self, market_id: str):
        """
        获取市场详细信息（用于套利监控）

        Args:
            market_id: 市场 ID（condition_id 或 question_id）
        """
        try:
            # 检查缓存
            if market_id in self._market_info_cache:
                cached_info, cached_time = self._market_info_cache[market_id]
                if time.time() - cached_time < self._cache_duration:
                    return cached_info

            # 获取市场列表
            markets = self.get_all_markets(limit=500)

            # 查找指定市场
            for market in markets:
                condition_id = market.get('condition_id')
                if condition_id == market_id or market.get('question_id') == market_id:
                    # 获取精确价格
                    token_price = self.get_market_price(condition_id, "BUY")
                    if token_price is None:
                        # 回退到市场数据中的价格
                        token_price = market.get('price', 0.5)
                        if isinstance(token_price, str):
                            token_price = float(token_price)
                        token_price = max(0.01, min(0.99, token_price))

                    @dataclass
                    class MarketInfo:
                        question_id: str
                        question_title: str
                        current_price: float
                        condition_id: str
                        volume: float
                        end_date: Optional[str]

                    info = MarketInfo(
                        question_id=market.get('question_id', condition_id),
                        question_title=market.get('question', 'Unknown Market'),
                        current_price=round(token_price, 4),
                        condition_id=condition_id,
                        volume=float(market.get('volume', 0)),
                        end_date=market.get('end_date')
                    )

                    # 更新缓存
                    self._market_info_cache[market_id] = (info, time.time())

                    return info

            # 如果没找到，返回默认值
            @dataclass
            class MarketInfo:
                question_id: str
                question_title: str
                current_price: float
                condition_id: str
                volume: float
                end_date: Optional[str]

            return MarketInfo(
                question_id=market_id,
                question_title=f"Market {market_id[:8]}",
                current_price=0.5,
                condition_id=market_id,
                volume=0.0,
                end_date=None
            )

        except Exception as e:
            self.logger.error(f"获取市场信息失败 {market_id}: {e}")
            # 返回默认值
            @dataclass
            class MarketInfo:
                question_id: str
                question_title: str
                current_price: float
                condition_id: str
                volume: float
                end_date: Optional[str]

            return MarketInfo(
                question_id=market_id,
                question_title=f"Market {market_id[:8]}",
                current_price=0.5,
                condition_id=market_id,
                volume=0.0,
                end_date=None
            )

    def get_order_book(self, market_id: str) -> Optional[PolymarketOrderBook]:
        """
        获取订单簿数据（用于套利监控）

        Args:
            market_id: 市场 ID
        """
        try:
            # 尝试从 CLOB API 获取真实订单簿
            if self.client:
                try:
                    # 获取市场的订单簿
                    order_book = self.client.get_order_book(market_id)

                    if order_book and isinstance(order_book, dict):
                        # 解析 Yes token 订单簿
                        yes_token_id = f"{market_id}_YES"

                        # 获取买一卖一
                        bids = order_book.get('bids', [])
                        asks = order_book.get('asks', [])

                        yes_bid = 0.49
                        yes_ask = 0.51
                        bid_size = 100.0
                        ask_size = 100.0

                        if bids:
                            best_bid = bids[0]
                            yes_bid = float(best_bid.get('price', yes_bid))
                            # 价格转换（如果需要）
                            if yes_bid > 1:
                                yes_bid = yes_bid / 10000
                            bid_size = float(best_bid.get('size', 100))

                        if asks:
                            best_ask = asks[0]
                            yes_ask = float(best_ask.get('price', yes_ask))
                            if yes_ask > 1:
                                yes_ask = yes_ask / 10000
                            ask_size = float(best_ask.get('size', 100))

                        return PolymarketOrderBook(
                            yes_bid=round(max(0.01, yes_bid), 4),
                            yes_ask=round(min(0.99, yes_ask), 4),
                            yes_bid_size=bid_size,
                            yes_ask_size=ask_size
                        )

                except Exception as e:
                    self.logger.debug(f"CLOB API 获取订单簿失败: {e}")

            # 回退：使用价格 API 估算订单簿
            yes_price = self.get_market_price(market_id, "BUY")
            no_price = self.get_market_price(market_id, "SELL")

            if yes_price and no_price:
                # Yes 价格即为买价，No 价格的倒数即为卖价
                return PolymarketOrderBook(
                    yes_bid=round(yes_price, 4),
                    yes_ask=round(1 - no_price, 4),
                    yes_bid_size=100.0,
                    yes_ask_size=100.0
                )

            # 最后回退：使用市场数据估算
            market = self.get_market_info(market_id)
            spread = 0.01  # 默认 1% 价差
            base_price = market.current_price

            return PolymarketOrderBook(
                yes_bid=round(max(0.01, base_price - spread / 2), 4),
                yes_ask=round(min(0.99, base_price + spread / 2), 4),
                yes_bid_size=100.0,
                yes_ask_size=100.0
            )

        except Exception as e:
            self.logger.error(f"获取订单簿失败 {market_id}: {e}")
            return None

    def search_markets(self, keyword: str = "", active_only: bool = True, limit: int = 50) -> List[Dict]:
        """
        搜索市场

        Args:
            keyword: 搜索关键词
            active_only: 是否只显示活跃市场
            limit: 返回数量限制
        """
        markets = self.get_all_markets(limit=500, active_only=active_only)

        if not keyword:
            return markets[:limit]

        # 搜索
        results = []
        keyword_lower = keyword.lower()

        for market in markets:
            question = market.get('question', '').lower()
            description = market.get('description', '').lower()
            tags = market.get('tags', [])

            # 匹配标题、描述或标签
            if (keyword_lower in question or
                keyword_lower in description or
                any(keyword_lower in str(t).lower() for t in tags)):
                results.append(market)

            if len(results) >= limit:
                break

        self.logger.info(f"搜索 '{keyword}': 找到 {len(results)} 个结果")
        return results

    def get_active_markets(self, limit: int = 50) -> List[Dict]:
        """
        获取活跃市场

        Args:
            limit: 返回数量
        """
        return self.get_all_markets(limit=limit, active_only=True)

    def clear_cache(self):
        """清除所有缓存"""
        self._markets_cache.clear()
        self._market_info_cache.clear()
        self._price_cache.clear()
        self._cache_time = 0
        self.logger.info("Polymarket 缓存已清除")


class MockPolymarketClient:
    """模拟客户端（用于测试）"""

    def __init__(self, config: Dict):
        self.config = config
        self.base_price = 0.50
        self._markets = self._generate_mock_markets()

    def _generate_mock_markets(self) -> List[Dict]:
        """生成模拟市场数据"""
        return [
            {
                'question_id': 'test-market-1',
                'question': 'Will Trump win the 2024 election?',
                'condition_id': 'test-condition-1',
                'active': True,
                'price': 0.55,
                'volume': 50000,
                'end_date': '2024-11-05T00:00:00Z',
                'tags': ['politics', 'election']
            },
            {
                'question_id': 'test-market-2',
                'question': 'Bitcoin will reach $100k in 2026',
                'condition_id': 'test-condition-2',
                'active': True,
                'price': 0.35,
                'volume': 25000,
                'end_date': '2026-12-31T00:00:00Z',
                'tags': ['crypto', 'bitcoin']
            }
        ]

    def get_all_markets(self, limit: int = 100, active_only: bool = True) -> List[Dict]:
        markets = self._markets
        if active_only:
            markets = [m for m in markets if m.get('active', True)]
        return markets[:limit]

    def get_popular_markets(self, limit: int = 20, min_volume: float = 1000) -> List[Dict]:
        return sorted(self._markets, key=lambda m: m.get('volume', 0), reverse=True)[:limit]

    def get_market_info(self, market_id: str):
        change = random.uniform(-0.015, 0.015)
        self.base_price = max(0.01, min(0.99, self.base_price * (1 + change)))

        from dataclasses import dataclass

        @dataclass
        class MarketInfo:
            question_id: str
            question_title: str
            current_price: float
            condition_id: str
            volume: float
            end_date: Optional[str]

        return MarketInfo(
            question_id=market_id,
            question_title='测试市场：某事件将在2026年发生',
            current_price=round(self.base_price, 3),
            condition_id=market_id,
            volume=1000.0,
            end_date=None
        )

    def get_order_book(self, market_id: str) -> Optional[PolymarketOrderBook]:
        change = random.uniform(-0.015, 0.015)
        self.base_price = max(0.01, min(0.99, self.base_price * (1 + change)))

        spread = random.uniform(0.005, 0.02)
        return PolymarketOrderBook(
            yes_bid=round(self.base_price - spread / 2, 3),
            yes_ask=round(self.base_price + spread / 2, 3),
            yes_bid_size=random.uniform(100, 1000),
            yes_ask_size=random.uniform(100, 1000)
        )

    def get_market_price(self, token_id: str, side: str = "BUY") -> Optional[float]:
        change = random.uniform(-0.015, 0.015)
        self.base_price = max(0.01, min(0.99, self.base_price * (1 + change)))
        return round(self.base_price, 3)

    def search_markets(self, keyword: str = "", active_only: bool = True, limit: int = 50) -> List[Dict]:
        return self.get_all_markets(limit, active_only)


def create_polymarket_client(config: Dict, use_real: bool = False):
    """
    创建 Polymarket 客户端

    Args:
        config: 配置字典
        use_real: 是否使用真实API（默认False使用模拟）
    """
    if use_real:
        return RealPolymarketClient(config)
    else:
        return MockPolymarketClient(config)
