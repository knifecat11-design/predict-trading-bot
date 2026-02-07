"""
API 客户端模块 - 精简版
负责与 predict.fun 平台通信
支持真实 API 和模拟模式
"""

import time
import random
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class MarketData:
    """市场数据"""
    market_id: str
    question: str
    current_price: float           # 中间价（仅用于显示）
    yes_bid: float                 # 买一价（实际可成交）
    yes_ask: float                 # 卖一价（实际可成交）
    best_bid_size: float
    best_ask_size: float
    liquidity: float
    volume: float
    timestamp: float


@dataclass
class Order:
    """订单信息"""
    order_id: str
    side: str
    price: float
    size: float
    status: str
    timestamp: float


class PredictAPIClient:
    """Predict.fun API 客户端"""

    def __init__(self, config: Dict):
        self.config = config
        self.api_key = config.get('api', {}).get('api_key', '')
        self.base_url = config.get('api', {}).get('base_url', 'https://api.predict.fun')

        import requests
        self.session = requests.Session()

        if self.api_key:
            self.session.headers.update({
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            })
            logger.info(f"Predict.fun API: {self.base_url} (已配置认证)")
        else:
            logger.warning("未设置 API Key")

        # 缓存
        self._cache: List[Dict] = []
        self._cache_time = 0
        self._cache_duration = config.get('api', {}).get('cache_seconds', 30)

    def get_markets(self, status: str = 'open', sort: str = 'popular', limit: int = 100) -> List[Dict]:
        """获取市场列表"""
        try:
            if time.time() - self._cache_time < self._cache_duration and self._cache:
                return self._cache[:limit]

            params = {'status': status, 'sort': sort, 'limit': min(limit, 100)}
            response = self.session.get(f"{self.base_url}/markets", params=params, timeout=15)

            if response.status_code == 200:
                data = response.json()
                markets = data if isinstance(data, list) else data.get('data', data.get('markets', []))

                if markets:
                    self._cache = markets
                    self._cache_time = time.time()
                    return markets[:limit]

            elif response.status_code == 401:
                logger.error("API 认证失败，请检查 API Key 或在网站上下单激活")

            return self._cache[:limit] if self._cache else []

        except Exception as e:
            logger.error(f"获取市场失败: {e}")
            return self._cache if self._cache else []

    def get_market_data(self, market_id: Optional[str] = None) -> Optional[MarketData]:
        """获取市场数据（使用订单簿价格）"""
        try:
            if not market_id:
                markets = self.get_markets(status='open', sort='popular', limit=10)
                if not markets:
                    return self._default_data('default')
                market = markets[0]
                market_id = market.get('id', market.get('market_id', 'default'))
            else:
                markets = self.get_markets(status='open', sort='popular', limit=100)
                market = next((m for m in markets if m.get('id') == market_id or m.get('market_id') == market_id), None)
                if not market:
                    return self._default_data(market_id)

            question = market.get('question') or market.get('title', 'Unknown')
            liquidity = float(market.get('liquidity', 0) or 0)
            volume = float(market.get('volume', 0) or market.get('volume24h', 0) or 0)

            # 优先使用 orderBook（真实可成交价格）
            orderbook = market.get('orderBook', {})
            bids = orderbook.get('bids', [])
            asks = orderbook.get('asks', [])

            if bids and asks:
                yes_bid = float(bids[0]['price'])
                yes_ask = float(asks[0]['price'])
                bid_size = float(bids[0].get('amount', bids[0].get('size', 100)))
                ask_size = float(asks[0].get('amount', asks[0].get('size', 100)))
            else:
                # 回退：调用订单簿 API
                orderbook = self._get_orderbook(market_id)
                yes_bid, yes_ask = orderbook['yes_bid'], orderbook['yes_ask']
                bid_size, ask_size = orderbook['bid_size'], orderbook['ask_size']

            return MarketData(
                market_id=market_id,
                question=question,
                current_price=(yes_bid + yes_ask) / 2,
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                best_bid_size=bid_size,
                best_ask_size=ask_size,
                liquidity=liquidity,
                volume=volume,
                timestamp=time.time()
            )

        except Exception as e:
            logger.error(f"获取市场数据失败: {e}")
            return self._default_data(market_id or 'default')

    def _get_orderbook(self, market_id: str) -> Dict:
        """获取订单簿"""
        try:
            response = self.session.get(f"{self.base_url}/markets/{market_id}/orderbook", timeout=10)
            if response.status_code == 200:
                data = response.json()
                bids = data.get('bids', [])
                asks = data.get('asks', [])

                return {
                    'yes_bid': float(bids[0]['price']) if bids else 0.49,
                    'yes_ask': float(asks[0]['price']) if asks else 0.51,
                    'bid_size': float(bids[0].get('amount', 100)) if bids else 100,
                    'ask_size': float(asks[0].get('amount', 100)) if asks else 100
                }
        except:
            pass

        return {'yes_bid': 0.49, 'yes_ask': 0.51, 'bid_size': 100, 'ask_size': 100}

    def _default_data(self, market_id: str) -> MarketData:
        return MarketData(market_id, 'Default', 0.5, 0.49, 0.51, 100, 100, 0, 0, time.time())

    def get_open_orders(self, market_id: Optional[str] = None) -> List[Order]:
        """获取挂单"""
        if not self.api_key:
            return []

        try:
            params = {'market_id': market_id} if market_id else {}
            response = self.session.get(f"{self.base_url}/orders", params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                orders = data if isinstance(data, list) else data.get('data', data.get('orders', []))

                return [
                    Order(
                        order_id=str(o.get('id', '')),
                        side=o.get('side', 'buy'),
                        price=float(o.get('price', 0)),
                        size=float(o.get('amount', o.get('size', 0))),
                        status=o.get('status', 'open'),
                        timestamp=time.time()
                    )
                    for o in orders if isinstance(o, dict)
                ]
        except Exception as e:
            logger.error(f"获取订单失败: {e}")

        return []

    def place_order(self, side: str, price: float, size: float, market_id: Optional[str] = None) -> Optional[Order]:
        """下单"""
        if not self.api_key:
            return None

        try:
            if not market_id:
                markets = self.get_markets(status='open', sort='popular', limit=1)
                if markets:
                    market_id = markets[0].get('id', 'default')

            payload = {'market_id': market_id, 'side': side.lower(), 'price': price, 'amount': size, 'type': 'limit'}
            response = self.session.post(f"{self.base_url}/orders", json=payload, timeout=15)

            if response.status_code in [200, 201]:
                data = response.json()
                logger.info(f"下单成功: {side} {size} @ {price}")
                return Order(str(data.get('id', '')), side, price, size, 'open', time.time())

        except Exception as e:
            logger.error(f"下单失败: {e}")

        return None

    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        if not self.api_key:
            return False

        try:
            response = self.session.delete(f"{self.base_url}/orders/{order_id}", timeout=10)
            if response.status_code in [200, 204]:
                logger.info(f"撤单成功: {order_id}")
                return True
        except Exception as e:
            logger.error(f"撤单失败: {e}")

        return False

    def cancel_all_orders(self, market_id: Optional[str] = None) -> int:
        """撤销所有挂单"""
        orders = self.get_open_orders(market_id)
        return sum(1 for o in orders if self.cancel_order(o.order_id))

    def clear_cache(self):
        """清除缓存"""
        self._cache.clear()
        self._cache_time = 0


class MockAPIClient:
    """模拟客户端"""

    def __init__(self, config: Dict):
        self.config = config
        self.market_id = config.get('market', {}).get('market_id', 'test-market')
        self.base_price = 0.50
        self._orders = {}
        self._counter = 0

    def get_markets(self, status: str = 'open', sort: str = 'popular', limit: int = 100) -> List[Dict]:
        return self._mock_markets()[:limit]

    def _mock_markets(self) -> List[Dict]:
        import random
        return [
            {
                'id': f'predict-{i}',
                'question': t['question'],
                'orderBook': {
                    'bids': [{'price': round(max(0.01, t['price'] - 0.02), 2), 'amount': random.randint(100, 1000)}],
                    'asks': [{'price': round(min(0.99, t['price'] + 0.02), 2), 'amount': random.randint(100, 1000)}]
                },
                'liquidity': random.randint(10000, 100000),
                'volume': random.randint(50000, 500000)
            }
            for i, t in enumerate([
                {'question': 'Will Trump win 2024?', 'price': 0.55},
                {'question': 'Bitcoin $100k in 2026?', 'price': 0.65},
                {'question': 'Fed rate below 3%?', 'price': 0.55}
            ])
        ]

    def get_market_data(self) -> MarketData:
        return MarketData(self.market_id, 'Mock', 0.5, 0.49, 0.51, 100, 100, 0, 0, time.time())

    def get_open_orders(self) -> List[Order]:
        return list(self._orders.values())

    def place_order(self, side: str, price: float, size: float) -> Order:
        self._counter += 1
        order = Order(f"order_{self._counter}", side, price, size, 'open', time.time())
        self._orders[order.order_id] = order
        return order

    def cancel_order(self, order_id: str) -> bool:
        return order_id in self._orders and bool(self._orders.pop(order_id, None))

    def cancel_all_orders(self) -> int:
        count = len(self._orders)
        self._orders.clear()
        return count


def create_api_client(config: Dict, use_mock: bool = False):
    """创建 API 客户端"""
    if use_mock:
        logger.info("使用 Predict.fun 模拟客户端")
        return MockAPIClient(config)
    else:
        logger.info("使用 Predict.fun 真实 API 客户端")
        return PredictAPIClient(config)
