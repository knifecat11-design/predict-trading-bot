"""
Probable.markets API 客户端模块
负责与 Probable.markets 平台通信
支持真实 API 和模拟模式

API 文档: https://developer.probable.markets/
"""

import time
import random
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ProbableMarketData:
    """Probable.markets 市场数据"""
    market_id: str
    question: str
    current_price: float
    yes_bid: float
    yes_ask: float
    best_bid_size: float
    best_ask_size: float
    timestamp: float


class ProbableAPIClient:
    """
    真实的 Probable.markets API 客户端

    认证方式：L1 Authentication (EIP-712 签名)
    需要钱包地址和签名
    """

    def __init__(self, config: Dict):
        self.config = config
        self.api_key = config.get('probable', {}).get('api_key', '')
        self.secret = config.get('probable', {}).get('secret', '')
        self.passphrase = config.get('probable', {}).get('passphrase', '')
        self.wallet_address = config.get('probable', {}).get('wallet_address', '')
        self.chain_id = config.get('probable', {}).get('chain_id', 1)  # 默认主网

        self.base_url = config.get('probable', {}).get('base_url', 'https://api.probable.markets')
        self.api_version = 'v1'

        # 设置会话
        import requests
        self.session = requests.Session()

        # 设置认证头
        if self.api_key:
            self.session.headers.update({
                'x-api-key': self.api_key,
                'Content-Type': 'application/json'
            })
        else:
            logger.warning("未设置 Probable API Key，某些功能可能无法使用")

        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

        logger.info(f"Probable.markets API 客户端初始化: {self.base_url}")

    def get_markets(self, active_only: bool = True) -> List[Dict]:
        """
        获取市场列表

        Args:
            active_only: 是否只返回活跃市场
        """
        try:
            # TODO: 确认正确的端点路径
            # 可能的端点：
            # - /public/api/v1/markets
            # - /api/v1/markets
            # - /markets

            response = self.session.get(
                f"{self.base_url}/{self.api_version}/markets",
                params={'active': active_only} if active_only else {},
                timeout=15
            )
            response.raise_for_status()
            data = response.json()

            # 处理不同的响应格式
            if isinstance(data, dict):
                markets = data.get('items', data.get('data', data.get('markets', [])))
                if not isinstance(markets, list):
                    logger.warning(f"API 返回了意外的格式: {type(data)}")
                    markets = []
            elif isinstance(data, list):
                markets = data
            else:
                logger.warning(f"API 返回了非列表/字典格式: {type(data)}")
                markets = []

            logger.info(f"Probable.markets: 获取到 {len(markets)} 个市场")
            return markets

        except Exception as e:
            logger.error(f"获取 Probable.markets 市场列表失败: {e}")
            return []

    def get_market_data(self, market_id: Optional[str] = None) -> Optional[ProbableMarketData]:
        """
        获取市场数据（用于套利监控）

        Args:
            market_id: 市场ID
        """
        try:
            if not market_id:
                # 如果没有指定market_id，使用默认市场或返回模拟数据
                return self._get_default_market_data()

            # 获取市场列表
            markets = self.get_markets(active_only=True)

            if not markets:
                return self._get_default_market_data()

            # 查找指定市场
            target_market = None
            for market in markets:
                if not isinstance(market, dict):
                    continue

                if market.get('id') == market_id or market.get('slug') == market_id:
                    target_market = market
                    break

            if not target_market:
                # 没找到指定市场，返回默认数据
                return self._get_default_market_data()

            # 解析市场数据
            current_price = self._parse_price(target_market.get('price', 0.5))

            # 获取订单簿数据
            orderbook = self.get_order_book(market_id)
            yes_bid = orderbook.get('yes_bid', current_price * 0.98)
            yes_ask = orderbook.get('yes_ask', current_price * 1.02)

            return ProbableMarketData(
                market_id=market_id,
                question=target_market.get('question', target_market.get('title', 'Unknown')),
                current_price=current_price,
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                best_bid_size=orderbook.get('bid_size', 100),
                best_ask_size=orderbook.get('ask_size', 100),
                timestamp=time.time()
            )

        except Exception as e:
            self.logger.error(f"获取 Probable.markets 市场数据失败: {e}")
            return self._get_default_market_data()

    def _get_default_market_data(self) -> Optional[ProbableMarketData]:
        """返回默认市场数据"""
        return ProbableMarketData(
            market_id='default-market',
            question='Default Market',
            current_price=0.5,
            yes_bid=0.49,
            yes_ask=0.51,
            best_bid_size=100,
            best_ask_size=100,
            timestamp=time.time()
        )

    def _parse_price(self, price) -> float:
        """解析价格"""
        if isinstance(price, (int, float)):
            return float(max(0.01, min(0.99, price)))
        if isinstance(price, str):
            try:
                return float(max(0.01, min(0.99, float(price))))
            except:
                pass
        return 0.5

    def get_order_book(self, market_id: str) -> Dict:
        """
        获取订单簿

        Args:
            market_id: 市场ID
        """
        try:
            # TODO: 确认正确的端点路径
            # 可能的端点：
            # - /public/api/v1/markets/{id}/orderbook
            # - /api/v1/orderbook/{market_id}
            # - /orderbook/{market_id}

            response = self.session.get(
                f"{self.base_url}/{self.api_version}/markets/{market_id}/orderbook",
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()

                # 解析订单簿数据
                bids = data.get('bids', data.get('yes_bids', []))
                asks = data.get('asks', data.get('no_asks', []))

                yes_bid = float(bids[0]['price']) if bids else 0.49
                yes_ask = float(asks[0]['price']) if asks else 0.51

                return {
                    'yes_bid': yes_bid,
                    'yes_ask': yes_ask,
                    'bid_size': float(bids[0]['amount']) if bids else 100,
                    'ask_size': float(asks[0]['amount']) if asks else 100
                }

        except Exception as e:
            logger.debug(f"获取 Probable.markets 订单簿失败 {market_id}: {e}")

        # 返回默认值
        return {
            'yes_bid': 0.49,
            'yes_ask': 0.51,
            'bid_size': 100,
            'ask_size': 100
        }


class MockProbableClient:
    """
    模拟 Probable.markets API 客户端
    用于测试策略逻辑
    """

    def __init__(self, config: Dict):
        self.config = config
        self.market_id = config.get('probable', {}).get('market_id', 'probable-test-market')
        self.base_price = 0.50

        # 模拟价格历史
        self._price_history = [self.base_price]

    def get_market_data(self, market_id: Optional[str] = None) -> Optional[ProbableMarketData]:
        """获取当前市场数据（模拟）"""
        # 模拟价格随机波动 ±2%
        change = random.uniform(-0.02, 0.02)
        self.base_price = max(0.01, min(0.99, self.base_price * (1 + change)))
        self._price_history.append(self.base_price)

        # 模拟买卖价差
        spread = random.uniform(0.01, 0.03)
        yes_bid = round(self.base_price - spread / 2, 3)
        yes_ask = round(self.base_price + spread / 2, 3)

        mid = market_id or self.market_id

        return ProbableMarketData(
            market_id=mid,
            question=f"Probable 测试市场 {mid}",
            current_price=round(self.base_price, 3),
            yes_bid=max(0.01, yes_bid),
            yes_ask=min(0.99, yes_ask),
            best_bid_size=random.uniform(100, 1000),
            best_ask_size=random.uniform(100, 1000),
            timestamp=time.time()
        )

    def get_markets(self, active_only: bool = True) -> List[Dict]:
        """获取市场列表（模拟）"""
        return [{
            'id': 'probable-test-market-1',
            'slug': 'probable-test',
            'question': 'Probable 测试：某事件将在2026年发生',
            'price': self.base_price,
            'active': True
        }]

    def get_order_book(self, market_id: str) -> Dict:
        """获取订单簿（模拟）"""
        spread = random.uniform(0.01, 0.03)
        return {
            'yes_bid': round(self.base_price - spread / 2, 3),
            'yes_ask': round(self.base_price + spread / 2, 3),
            'bid_size': random.uniform(100, 1000),
            'ask_size': random.uniform(100, 1000)
        }


def create_probable_client(config: Dict, use_mock: bool = True):
    """
    创建 Probable.markets API 客户端工厂函数

    Args:
        config: 配置字典
        use_mock: 是否使用模拟客户端（默认True）

    Returns:
        API 客户端实例
    """
    if use_mock:
        logger.info("使用 Probable.markets 模拟客户端")
        return MockProbableClient(config)
    else:
        logger.info("使用 Probable.markets 真实 API 客户端")
        return ProbableAPIClient(config)
