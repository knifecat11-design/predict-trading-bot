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
            cache_key = f"{status}:{sort}"
            if time.time() - self._cache_time < self._cache_duration and self._cache and getattr(self, '_cache_key', '') == cache_key:
                return self._cache[:limit]

            params = {'status': status, 'sort': sort, 'limit': min(limit, 100)}
            response = self.session.get(f"{self.base_url}/markets", params=params, timeout=15)

            if response.status_code == 200:
                data = response.json()
                markets = data if isinstance(data, list) else data.get('data', data.get('markets', []))

                if markets:
                    self._cache = markets
                    self._cache_key = cache_key
                    self._cache_time = time.time()
                    return markets[:limit]
                else:
                    logger.warning(f"Predict API returned 200 but no markets (response keys: {list(data.keys()) if isinstance(data, dict) else 'list'})")

            elif response.status_code == 401:
                logger.error("Predict API 401: 认证失败，请检查 API Key 或在网站上下单激活")
            else:
                logger.error(f"Predict API HTTP {response.status_code}: {response.text[:300]}")

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

    def _get_orderbook(self, market_id: str, outcome_id: int = 1) -> Dict:
        """
        获取订单簿

        Args:
            market_id: 市场 ID
            outcome_id: 1=Yes token, 0=No token（默认 1）
        """
        try:
            # Predict.fun API 支持通过 outcomeId 参数获取不同 token 的订单簿
            params = {'outcomeId': outcome_id} if outcome_id != 1 else {}
            response = self.session.get(f"{self.base_url}/markets/{market_id}/orderbook",
                                   params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                bids = data.get('bids', [])
                asks = data.get('asks', [])

                # 检查订单簿是否有效（验证 outcomeId 参数是否被支持）
                if not bids and not asks:
                    logger.warning(f"订单簿为空 (outcome_id={outcome_id}, market={market_id})，API 可能不支持 outcomeId 参数")
                    return {'yes_bid': None, 'yes_ask': None, 'bid_size': 0, 'ask_size': 0}

                return {
                    'yes_bid': float(bids[0]['price']) if bids else None,
                    'yes_ask': float(asks[0]['price']) if asks else None,
                    'bid_size': float(bids[0].get('amount', 100)) if bids else 0,
                    'ask_size': float(asks[0].get('amount', 100)) if asks else 0
                }
        except Exception as e:
            logger.debug(f"获取订单簿失败 (outcome_id={outcome_id}): {e}")

        # 返回 None 表示获取失败，而不是假数据
        return {'yes_bid': None, 'yes_ask': None, 'bid_size': 0, 'ask_size': 0}

    def get_full_orderbook(self, market_id: str) -> Optional[Dict]:
        """
        获取完整订单簿（Yes 和 No token）

        Returns:
            {'yes_bid': float, 'yes_ask': float, 'no_bid': float, 'no_ask': float}
            或 None（如果任一 token 订单簿获取失败）
        """
        yes_ob = self._get_orderbook(market_id, outcome_id=1)
        no_ob = self._get_orderbook(market_id, outcome_id=0)

        # 检查是否都有有效的买卖价
        if None in [yes_ob['yes_bid'], yes_ob['yes_ask'], no_ob['yes_bid'], no_ob['yes_ask']]:
            logger.debug(f"市场 {market_id} 订单簿不完整")
            return None

        # 验证 No token 订单簿是否真的不同于 Yes（防止 API 不支持 outcomeId）
        # 如果价格相同或非常接近，说明 API 忽略了 outcomeId 参数
        yes_mid = (yes_ob['yes_bid'] + yes_ob['yes_ask']) / 2 if yes_ob['yes_bid'] and yes_ob['yes_ask'] else None
        no_mid = (no_ob['yes_bid'] + no_ob['yes_ask']) / 2 if no_ob['yes_bid'] and no_ob['yes_ask'] else None

        if yes_mid and no_mid:
            diff_pct = abs(yes_mid - no_mid) / yes_mid * 100 if yes_mid > 0 else 100
            if diff_pct < 1:  # 差异小于 1%，认为 API 返回了相同数据
                logger.warning(f"市场 {market_id} 的 Yes/No 订单簿数据相同 (diff={diff_pct:.2f}%)，API 可能不支持 outcomeId 参数")
                # Fallback: 使用 1 - yes_price 推导 No 价格
                if yes_ob['yes_ask'] is not None:
                    no_bid = round(1.0 - yes_ob['yes_bid'], 4)
                    no_ask = round(1.0 - yes_ob['yes_ask'], 4)
                    return {
                        'yes_bid': yes_ob['yes_bid'],
                        'yes_ask': yes_ob['yes_ask'],
                        'no_bid': no_bid,
                        'no_ask': no_ask,
                    }

        return {
            'yes_bid': yes_ob['yes_bid'],
            'yes_ask': yes_ob['yes_ask'],
            'no_bid': no_ob['yes_bid'],
            'no_ask': no_ob['yes_ask'],
        }

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
