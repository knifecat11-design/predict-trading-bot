"""
Polymarket API 客户端模块
支持真实的 CLOB API 和模拟模式
"""

import time
import random
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.constants import POLYGON
    PY_CLOB_AVAILABLE = True
except ImportError:
    PY_CLOB_AVAILABLE = False


logger = logging.getLogger(__name__)


@dataclass
class PolymarketMarket:
    """Polymarket 市场信息"""
    question_id: str
    question_title: str
    current_price: float
    volume_24h: float
    end_date: Optional[str] = None


class RealPolymarketClient:
    """
    真实的 Polymarket API 客户端
    使用官方 py-clob-client 库
    API 文档: https://docs.polymarket.com/developers/CLOB/introduction
    """

    def __init__(self, config: Dict):
        self.config = config
        self.logger = logging.getLogger(__name__)

        # CLOB API 端点
        self.host = "api.polymarket.com"
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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.base_url = f"https://{self.host}"

        # 缓存
        self._markets_cache = {}
        self._cache_time = 0
        self._cache_duration = 30  # 缓存30秒

    def get_all_markets(self, limit: int = 100) -> List[Dict]:
        """
        获取所有市场列表（使用公开 API）

        API 文档: https://docs.polymarket.com/quickstart/fetching-data
        """
        try:
            # 使用公开的 Gamma API
            response = self.session.get(
                f"{self.base_url}/markets",
                params={'limit': limit},
                timeout=15
            )
            response.raise_for_status()
            markets = response.json()

            self.logger.info(f"获取到 {len(markets)} 个市场")
            return markets

        except Exception as e:
            self.logger.error(f"获取市场列表失败: {e}")
            return []

    def get_market_price(self, token_id: str, side: str = "BUY") -> Optional[float]:
        """
        获取指定 token 的价格

        Args:
            token_id: Token ID (condition_id)
            side: "BUY" 或 "SELL"
        """
        try:
            response = self.session.get(
                f"{self.base_url}/price",
                params={'token_id': token_id, 'side': side},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            price = float(data.get('price', 0))

            # Polymarket 价格可能很大，需要转换为 0-1 范围
            if price > 1:
                price = price / 10000

            return round(price, 3)
        except Exception as e:
            self.logger.error(f"获取价格失败 {token_id}: {e}")
            return None

    def get_market_info(self, market_id: str):
        """
        获取市场信息（用于套利监控）
        """
        try:
            # 首先尝试从缓存获取
            if time.time() - self._cache_time < self._cache_duration:
                if market_id in self._markets_cache:
                    return self._markets_cache[market_id]

            # 获取市场列表
            markets = self.get_all_markets(limit=200)

            # 查找指定市场
            for market in markets:
                condition_id = market.get('condition_id')
                if condition_id == market_id or market.get('question_id') == market_id:
                    # 计算价格
                    token_price = market.get('price', 0.5)
                    if isinstance(token_price, str):
                        token_price = float(token_price)

                    @dataclass
                    class MarketInfo:
                        question_id: str
                        question_title: str
                        current_price: float

                    info = MarketInfo(
                        question_id=condition_id or market.get('question_id', ''),
                        question_title=market.get('question', 'Unknown Market'),
                        current_price=min(0.99, max(0.01, token_price))
                    )

                    # 更新缓存
                    self._markets_cache[market_id] = info
                    self._cache_time = time.time()

                    return info

            # 如果没找到，返回默认值
            @dataclass
            class MarketInfo:
                question_id: str
                question_title: str
                current_price: float

            return MarketInfo(
                question_id=market_id,
                question_title=f"Market {market_id[:8]}",
                current_price=0.5
            )

        except Exception as e:
            self.logger.error(f"获取市场信息失败 {market_id}: {e}")
            # 返回默认值
            @dataclass
            class MarketInfo:
                question_id: str
                question_title: str
                current_price: float

            return MarketInfo(
                question_id=market_id,
                question_title=f"Market {market_id[:8]}",
                current_price=0.5
            )

    def get_order_book(self, market_id: str):
        """
        获取订单簿数据（用于套利监控）
        """
        try:
            # 获取市场数据
            markets = self.get_all_markets(limit=200)

            for market in markets:
                if market.get('condition_id') == market_id or market.get('question_id') == market_id:
                    token_price = market.get('price', 0.5)
                    if isinstance(token_price, str):
                        token_price = float(token_price)

                    # 模拟买卖价差
                    spread = random.uniform(0.005, 0.02)
                    yes_bid = max(0.01, token_price - spread / 2)
                    yes_ask = min(0.99, token_price + spread / 2)

                    @dataclass
                    class OrderBook:
                        yes_bid: float
                        yes_ask: float

                    return OrderBook(
                        yes_bid=round(yes_bid, 3),
                        yes_ask=round(yes_ask, 3)
                    )

            # 默认值
            @dataclass
            class OrderBook:
                yes_bid: float
                yes_ask: float

            return OrderBook(yes_bid=0.49, yes_ask=0.51)

        except Exception as e:
            self.logger.error(f"获取订单簿失败 {market_id}: {e}")
            @dataclass
            class OrderBook:
                yes_bid: float
                yes_ask: float

            return OrderBook(yes_bid=0.49, yes_ask=0.51)

    def search_markets(self, keyword: str = "", active_only: bool = True) -> List[Dict]:
        """
        搜索市场

        Args:
            keyword: 搜索关键词
            active_only: 是否只显示活跃市场
        """
        markets = self.get_all_markets(limit=200)

        if active_only:
            markets = [m for m in markets if m.get('active', True)]

        if keyword:
            results = []
            keyword_lower = keyword.lower()
            for market in markets:
                question = market.get('question', '').lower()
                description = market.get('description', '').lower()
                if keyword_lower in question or keyword_lower in description:
                    results.append(market)
            return results[:20]

        return markets[:20]

    def get_active_markets(self, limit: int = 20) -> List[Dict]:
        """获取活跃市场"""
        markets = self.get_all_markets(limit=200)
        active = [m for m in markets if m.get('active', True)]
        return active[:limit]


class MockPolymarketClient:
    """模拟客户端（用于测试）"""

    def __init__(self, config: Dict):
        self.config = config
        self.base_price = 0.50

    def get_all_markets(self) -> List[Dict]:
        return [{
            'question_id': 'test-market-1',
            'question': '测试市场：某事件将在2026年发生',
            'condition_id': 'test-condition-1',
            'active': True,
            'price': 0.5
        }]

    def get_market_info(self, market_id: str):
        """获取市场信息（模拟）"""
        change = random.uniform(-0.015, 0.015)
        self.base_price = max(0.01, min(0.99, self.base_price * (1 + change)))

        from dataclasses import dataclass

        @dataclass
        class MarketInfo:
            question_id: str
            question_title: str
            current_price: float

        return MarketInfo(
            question_id=market_id,
            question_title='测试市场：某事件将在2026年发生',
            current_price=round(self.base_price, 3)
        )

    def get_order_book(self, market_id: str):
        """获取订单簿（模拟）"""
        change = random.uniform(-0.015, 0.015)
        self.base_price = max(0.01, min(0.99, self.base_price * (1 + change)))

        from dataclasses import dataclass

        @dataclass
        class OrderBook:
            yes_bid: float
            yes_ask: float

        spread = random.uniform(0.005, 0.02)
        return OrderBook(
            yes_bid=round(self.base_price - spread / 2, 3),
            yes_ask=round(self.base_price + spread / 2, 3)
        )

    def get_market_price(self, token_id: str, side: str = "BUY") -> Optional[float]:
        change = random.uniform(-0.015, 0.015)
        self.base_price = max(0.01, min(0.99, self.base_price * (1 + change)))
        return round(self.base_price, 3)

    def search_markets(self, keyword: str = "") -> List[Dict]:
        return self.get_all_markets()


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
