"""
Polymarket API 客户端模块
使用真实的 CLOB API
"""

import requests
import time
import random
from typing import Dict, List, Optional
from dataclasses import dataclass


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
    API 文档: https://docs.polymarket.com
    """

    def __init__(self, config: Dict):
        self.config = config
        self.base_url = "https://clob.polymarket.com"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

        # 缓存市场数据
        self._markets_cache = {}
        self._cache_time = 0
        self._cache_duration = 60  # 缓存60秒

    def get_all_markets(self) -> List[Dict]:
        """
        获取所有市场列表

        Returns:
            市场列表
        """
        try:
            response = self.session.get(
                f"{self.base_url}/markets",
                params={'limit': 100},
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"获取市场列表失败: {e}")
            return []

    def get_market_price(self, token_id: str, side: str = "BUY") -> Optional[float]:
        """
        获取指定 token 的价格

        Args:
            token_id: Token ID (condition_id)
            side: "BUY" 或 "SELL"

        Returns:
            价格 (0-1之间的小数)
        """
        try:
            response = self.session.get(
                f"{self.base_url}/price",
                params={
                    'token_id': token_id,
                    'side': side
                },
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            price = float(data.get('price', 0))
            # Polymarket 价格可能很大，需要转换为 0-1 范围
            if price > 1:
                price = price / 10000  # 或者除以其他基数
            return round(price, 3)
        except Exception as e:
            print(f"获取价格失败: {e}")
            return None

    def get_order_book(self, token_id: str) -> Optional[Dict]:
        """
        获取订单簿

        Args:
            token_id: Token ID

        Returns:
            订单簿数据
        """
        try:
            response = self.session.get(
                f"{self.base_url}/orderbook",
                params={'token_id': token_id},
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"获取订单簿失败: {e}")
            return None

    def search_markets(self, keyword: str = "") -> List[Dict]:
        """
        搜索市场

        Args:
            keyword: 搜索关键词

        Returns:
            匹配的市场列表
        """
        markets = self.get_all_markets()

        if not keyword:
            return markets[:10]  # 返回前10个

        # 过滤匹配的市场
        results = []
        for market in markets:
            question = market.get('question', '').lower()
            if keyword.lower() in question:
                results.append(market)

        return results[:10]

    def get_active_markets(self) -> List[Dict]:
        """
        获取活跃的市场

        Returns:
            活跃市场列表
        """
        markets = self.get_all_markets()
        # 过滤活跃市场
        active = [m for m in markets if m.get('active', False)]
        return active[:20]


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
            'active': True
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
