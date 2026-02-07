"""
Kalshi API 客户端模块
Kalshi 是美国持牌的 CFTC 预测市场平台
API 文档: https://trading-api.kalshi.com/v1/docs
"""

import time
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import requests


logger = logging.getLogger(__name__)


@dataclass
class KalshiMarket:
    """Kalshi 市场信息"""
    market_id: str
    title: str
    current_price: float
    yes_price: float
    no_price: float
    volume: float
    liquidity: float
    close_time: Optional[str]


@dataclass
class KalshiOrderBook:
    """Kalshi 订单簿"""
    yes_bid: float
    yes_ask: float
    yes_bid_size: float
    yes_ask_size: float


class KalshiAPIClient:
    """
    Kalshi API 客户端
    注意：部分端点需要认证，但市场数据是公开的
    """

    def __init__(self, config: Dict):
        self.config = config
        self.base_url = "https://trading-api.kalshi.com/v1"

        # 设置会话
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Content-Type': 'application/json'
        })

        # 认证信息（可选，用于交易）
        self.api_key = config.get('kalshi', {}).get('api_key', '')
        if self.api_key:
            self.session.headers.update({
                'Authorization': f'Bearer {self.api_key}'
            })

        logger.info(f"Kalshi API 客户端初始化: {self.base_url}")

        # 缓存
        self._markets_cache: List[Dict] = []
        self._cache_time = 0
        self._cache_duration = config.get('kalshi', {}).get('cache_seconds', 30)

    def get_markets(self, limit: int = 1000, status: str = 'open') -> List[Dict]:
        """
        获取市场列表

        Args:
            limit: 返回数量限制
            status: 市场状态 ('open', 'closed', 'all')

        Returns:
            市场列表
        """
        try:
            # 检查缓存
            if time.time() - self._cache_time < self._cache_duration and self._markets_cache:
                logger.debug(f"使用缓存的市场列表 ({len(self._markets_cache)} 个)")
                return self._markets_cache[:limit]

            # 获取市场列表
            params = {
                'limit': min(limit, 1000),
                'status': status
            }

            response = self.session.get(
                f"{self.base_url}/markets",
                params=params,
                timeout=15
            )
            response.raise_for_status()
            data = response.json()

            # Kalshi API 返回格式: {markets: [...]}
            markets = data.get('markets', data.get('data', []))

            logger.info(f"获取到 {len(markets)} 个 Kalshi 市场")

            # 更新缓存
            self._markets_cache = markets
            self._cache_time = time.time()

            return markets[:limit]

        except Exception as e:
            logger.error(f"获取 Kalshi 市场列表失败: {e}")
            return self._markets_cache if self._markets_cache else []

    def get_market_info(self, market_id: str) -> Optional[KalshiMarket]:
        """
        获取市场详细信息

        Args:
            market_id: 市场 ID

        Returns:
            KalshiMarket 对象或 None
        """
        try:
            response = self.session.get(
                f"{self.base_url}/markets/{market_id}",
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            market = data.get('market', data)

            # 解析价格
            yes_price = float(market.get('yes_price', 0.5))
            no_price = 1.0 - yes_price

            return KalshiMarket(
                market_id=market_id,
                title=market.get('title', market.get('question', 'Unknown')),
                current_price=yes_price,
                yes_price=yes_price,
                no_price=no_price,
                volume=float(market.get('volume', 0)),
                liquidity=float(market.get('liquidity', 0)),
                close_time=market.get('close_time')
            )

        except Exception as e:
            logger.error(f"获取 Kalshi 市场信息失败 {market_id}: {e}")
            return None

    def get_order_book(self, market_id: str) -> Optional[KalshiOrderBook]:
        """
        获取订单簿

        Args:
            market_id: 市场 ID

        Returns:
            KalshiOrderBook 对象或 None
        """
        try:
            response = self.session.get(
                f"{self.base_url}/markets/{market_id}/orderbook",
                timeout=10
            )

            if response.status_code != 200:
                # 如果无法获取订单簿，返回估算值
                market = self.get_market_info(market_id)
                if market:
                    spread = max(0.01, market.yes_price * 0.02)
                    return KalshiOrderBook(
                        yes_bid=round(max(0.01, market.yes_price - spread / 2), 2),
                        yes_ask=round(min(0.99, market.yes_price + spread / 2), 2),
                        yes_bid_size=100.0,
                        yes_ask_size=100.0
                    )
                return None

            data = response.json()

            # 解析订单簿
            bids = data.get('bids', [])
            asks = data.get('asks', [])

            yes_bid = float(bids[0]['price']) if bids else 0.49
            yes_ask = float(asks[0]['price']) if asks else 0.51
            bid_size = float(bids[0]['total_volume']) if bids else 100
            ask_size = float(asks[0]['total_volume']) if asks else 100

            return KalshiOrderBook(
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                yes_bid_size=bid_size,
                yes_ask_size=ask_size
            )

        except Exception as e:
            logger.debug(f"获取 Kalshi 订单簿失败 {market_id}: {e}")

            # 回退：估算订单簿
            market = self.get_market_info(market_id)
            if market:
                spread = max(0.01, market.yes_price * 0.02)
                return KalshiOrderBook(
                    yes_bid=round(max(0.01, market.yes_price - spread / 2), 2),
                    yes_ask=round(min(0.99, market.yes_price + spread / 2), 2),
                    yes_bid_size=100.0,
                    yes_ask_size=100.0
                )

            return None

    def search_markets(self, keyword: str, limit: int = 50) -> List[Dict]:
        """
        搜索市场

        Args:
            keyword: 搜索关键词
            limit: 返回数量

        Returns:
            匹配的市场列表
        """
        try:
            markets = self.get_markets(limit=1000)

            if not keyword:
                return markets[:limit]

            # 搜索
            results = []
            keyword_lower = keyword.lower()

            for market in markets:
                title = market.get('title', market.get('question', '')).lower()
                description = market.get('description', '').lower()
                category = market.get('category', '').lower()

                if (keyword_lower in title or
                    keyword_lower in description or
                    keyword_lower in category):
                    results.append(market)

                if len(results) >= limit:
                    break

            logger.info(f"Kalshi 搜索 '{keyword}': 找到 {len(results)} 个结果")
            return results

        except Exception as e:
            logger.error(f"搜索 Kalshi 市场失败: {e}")
            return []

    def get_markets_by_category(self, category: str, limit: int = 50) -> List[Dict]:
        """
        按类别获取市场

        Args:
            category: 类别名称
            limit: 返回数量

        Returns:
            市场列表
        """
        try:
            markets = self.get_markets(limit=1000)

            # 过滤类别
            filtered = []
            category_lower = category.lower()

            for market in markets:
                market_category = market.get('category', '').lower()
                if category_lower in market_category:
                    filtered.append(market)

            logger.info(f"Kalshi 类别 '{category}': 找到 {len(filtered)} 个市场")
            return filtered[:limit]

        except Exception as e:
            logger.error(f"按类别获取 Kalshi 市场失败: {e}")
            return []

    def clear_cache(self):
        """清除缓存"""
        self._markets_cache.clear()
        self._cache_time = 0
        logger.info("Kalshi 缓存已清除")


class MockKalshiClient:
    """模拟 Kalshi 客户端（用于测试）"""

    def __init__(self, config: Dict):
        self.config = config
        self.base_price = 0.50

        # 模拟市场
        self._markets = self._generate_mock_markets()

    def _generate_mock_markets(self) -> List[Dict]:
        """生成模拟市场数据"""
        return [
            {
                'market_id': 'kalshi-1',
                'title': 'Will GDP growth exceed 3% in Q1 2026?',
                'yes_price': 0.45,
                'volume': 50000,
                'category': 'economics'
            },
            {
                'market_id': 'kalshi-2',
                'title': 'Will Fed cut interest rates by June 2026?',
                'yes_price': 0.65,
                'volume': 75000,
                'category': 'economics'
            },
            {
                'market_id': 'kalshi-3',
                'title': 'Will US unemployment exceed 6% in 2026?',
                'yes_price': 0.35,
                'volume': 30000,
                'category': 'economics'
            }
        ]

    def get_markets(self, limit: int = 100, status: str = 'open') -> List[Dict]:
        return self._markets[:limit]

    def get_market_info(self, market_id: str) -> Optional[KalshiMarket]:
        for market in self._markets:
            if market.get('market_id') == market_id:
                yes_price = market.get('yes_price', 0.5)
                return KalshiMarket(
                    market_id=market_id,
                    title=market.get('title', 'Unknown'),
                    current_price=yes_price,
                    yes_price=yes_price,
                    no_price=1.0 - yes_price,
                    volume=float(market.get('volume', 0)),
                    liquidity=1000.0,
                    close_time=None
                )
        return None

    def get_order_book(self, market_id: str) -> Optional[KalshiOrderBook]:
        import random
        spread = random.uniform(0.01, 0.03)
        return KalshiOrderBook(
            yes_bid=round(max(0.01, self.base_price - spread / 2), 3),
            yes_ask=round(min(0.99, self.base_price + spread / 2), 3),
            yes_bid_size=random.uniform(100, 1000),
            yes_ask_size=random.uniform(100, 1000)
        )

    def search_markets(self, keyword: str, limit: int = 50) -> List[Dict]:
        return self._markets[:limit]

    def clear_cache(self):
        pass


def create_kalshi_client(config: Dict, use_mock: bool = False):
    """
    创建 Kalshi 客户端

    Args:
        config: 配置字典
        use_mock: 是否使用模拟客户端

    Returns:
        Kalshi 客户端实例
    """
    if use_mock:
        logger.info("使用 Kalshi 模拟客户端")
        return MockKalshiClient(config)
    else:
        logger.info("使用 Kalshi 真实 API 客户端")
        return KalshiAPIClient(config)
