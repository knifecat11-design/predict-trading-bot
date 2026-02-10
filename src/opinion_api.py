"""
Opinion.trade API 客户端模块
API 文档: https://docs.opinion.trade/developer-guide/opinion-open-api/overview
"""

import time
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class OpinionMarket:
    """Opinion 市场信息"""
    market_id: str
    market_title: str
    yes_token_id: str
    no_token_id: str
    yes_price: float
    no_price: float
    volume: float
    volume_24h: float
    status: str


@dataclass
class OpinionOrderBook:
    """Opinion 订单簿"""
    yes_bid: float
    yes_ask: float
    yes_bid_size: float
    yes_ask_size: float


class OpinionAPIClient:
    """
    Opinion.trade API 客户端

    API 特点:
    - Base URL: https://proxy.opinion.trade:8443/openapi
    - 认证: apikey header
    - 速率限制: 15 req/s
    """

    def __init__(self, config: Dict):
        self.config = config

        # 获取 API key
        opinion_config = config.get('opinion', {})
        self.api_key = opinion_config.get('api_key', '')
        self.base_url = opinion_config.get('base_url', 'https://proxy.opinion.trade:8443/openapi')

        # 设置会话
        import requests
        self.session = requests.Session()

        if self.api_key:
            self.session.headers.update({
                'apikey': self.api_key,
                'Content-Type': 'application/json'
            })
            logger.info(f"Opinion API: {self.base_url} (已配置认证)")
        else:
            logger.warning("未设置 OPINION_API_KEY")

        # 缓存
        self._markets_cache: List[Dict] = []
        self._cache_time = 0
        self._cache_duration = opinion_config.get('cache_seconds', 30)

    def get_markets(self,
                    status: str = 'activated',
                    sort_by: int = 5,  # 按 24h 交易量排序
                    limit: int = 500) -> List[Dict]:
        """
        获取市场列表（分页抓取全站）

        Args:
            status: 市场状态 ('activated', 'closed', etc.)
            sort_by: 排序方式 (5 = 按 24h 交易量)
            limit: 返回数量限制

        Opinion API 每页最多 20 条，通过 offset 分页。
        """
        try:
            # 检查缓存
            if time.time() - self._cache_time < self._cache_duration and self._markets_cache:
                return self._markets_cache[:limit]

            all_markets = []
            page_size = 20  # Opinion API max per page
            max_pages = (limit + page_size - 1) // page_size

            for page in range(max_pages):
                params = {
                    'status': status,
                    'sortBy': sort_by,
                    'limit': page_size,
                    'offset': page * page_size,
                }

                response = self.session.get(
                    f"{self.base_url}/market",
                    params=params,
                    timeout=15
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get('code') == 0:
                        batch = data.get('result', {}).get('list', [])
                        if not batch:
                            break
                        all_markets.extend(batch)
                        if len(batch) < page_size:
                            break  # 最后一页
                    else:
                        logger.error(f"Opinion API 错误: {data.get('msg')}")
                        break
                elif response.status_code == 401:
                    logger.error("Opinion API 认证失败，请检查 API Key")
                    break
                elif response.status_code == 429:
                    logger.warning("Opinion API 速率限制，暂停获取")
                    break
                else:
                    logger.error(f"Opinion API HTTP {response.status_code}")
                    break

            if all_markets:
                self._markets_cache = all_markets
                self._cache_time = time.time()
                logger.info(f"Opinion: 获取到 {len(all_markets)} 个市场")

            return all_markets[:limit]

        except Exception as e:
            logger.error(f"获取 Opinion 市场失败: {e}")

        return self._markets_cache[:limit] if self._markets_cache else []

    def get_token_price(self, token_id: str) -> Optional[float]:
        """
        获取 Token 最新价格

        Args:
            token_id: Token ID

        Returns:
            价格或 None
        """
        try:
            params = {'token_id': token_id}
            response = self.session.get(
                f"{self.base_url}/token/latest-price",
                params=params,
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    result = data.get('result', {})
                    price = result.get('price')
                    if price:
                        return float(price)

        except Exception as e:
            logger.debug(f"获取 Token 价格失败 {token_id}: {e}")

        return None

    def get_order_book(self, token_id: str) -> Optional[OpinionOrderBook]:
        """
        获取订单簿

        Args:
            token_id: Token ID

        Returns:
            OpinionOrderBook 或 None
        """
        try:
            params = {'token_id': token_id}
            response = self.session.get(
                f"{self.base_url}/token/orderbook",
                params=params,
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('code') == 0:
                    result = data.get('result', {})

                    bids = result.get('bids', [])
                    asks = result.get('asks', [])

                    yes_bid = float(bids[0]['price']) if bids else 0.49
                    yes_ask = float(asks[0]['price']) if asks else 0.51
                    bid_size = float(bids[0]['size']) if bids else 100
                    ask_size = float(asks[0]['size']) if asks else 100

                    return OpinionOrderBook(
                        yes_bid=yes_bid,
                        yes_ask=yes_ask,
                        yes_bid_size=bid_size,
                        yes_ask_size=ask_size
                    )

        except Exception as e:
            logger.debug(f"获取订单簿失败 {token_id}: {e}")

        return None

    def get_market_info(self, market_id: str) -> Optional[OpinionMarket]:
        """
        获取市场详细信息

        Args:
            market_id: 市场 ID

        Returns:
            OpinionMarket 或 None
        """
        try:
            markets = self.get_markets()

            for market in markets:
                if str(market.get('marketId')) == str(market_id):
                    # 获取 Yes 和 No Token 价格
                    yes_token_id = market.get('yesTokenId')
                    no_token_id = market.get('noTokenId')

                    yes_price = self.get_token_price(yes_token_id) or 0.5
                    no_price = 1.0 - yes_price

                    return OpinionMarket(
                        market_id=str(market['marketId']),
                        market_title=market.get('marketTitle', 'Unknown'),
                        yes_token_id=yes_token_id,
                        no_token_id=no_token_id,
                        yes_price=yes_price,
                        no_price=no_price,
                        volume=float(market.get('volume', 0)),
                        volume_24h=float(market.get('volume24h', 0)),
                        status=market.get('statusEnum', 'Unknown')
                    )

        except Exception as e:
            logger.error(f"获取市场信息失败 {market_id}: {e}")

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
            markets = self.get_markets()

            if not keyword:
                return markets[:limit]

            keyword_lower = keyword.lower()
            results = []

            for market in markets:
                title = market.get('marketTitle', '').lower()

                if keyword_lower in title:
                    results.append(market)

                if len(results) >= limit:
                    break

            logger.info(f"Opinion 搜索 '{keyword}': 找到 {len(results)} 个结果")
            return results

        except Exception as e:
            logger.error(f"搜索市场失败: {e}")
            return []

    def clear_cache(self):
        """清除缓存"""
        self._markets_cache.clear()
        self._cache_time = 0
        logger.info("Opinion 缓存已清除")


class MockOpinionClient:
    """模拟 Opinion 客户端（用于测试）"""

    def __init__(self, config: Dict):
        self.config = config
        self.base_price = 0.50

    def get_markets(self, status: str = 'activated', sort_by: int = 5, limit: int = 100) -> List[Dict]:
        """获取模拟市场列表"""
        import random
        return [
            {
                'marketId': i + 1,
                'marketTitle': t['question'],
                'yesTokenId': f'0x{"1234567890abcdef" * 2}',
                'noTokenId': f'0x{"fedcba987654321" * 2}',
                'volume': '1500000.00',
                'volume24h': '125000.00',
                'statusEnum': 'Activated'
            }
            for i, t in enumerate([
                {'question': 'Will BTC reach $100k by end of 2025?'},
                {'question': 'Will ETH reach $10k by end of 2025?'},
                {'question': 'Will Fed cut rates below 3% in 2026?'},
                {'question': 'Will Trump win 2024 election?'},
                {'question': 'Will AI pass Turing test by 2027?'}
            ])
        ][:limit]

    def get_token_price(self, token_id: str) -> Optional[float]:
        """获取模拟价格"""
        import random
        change = random.uniform(-0.02, 0.02)
        self.base_price = max(0.01, min(0.99, self.base_price * (1 + change)))
        return self.base_price

    def get_order_book(self, token_id: str) -> Optional[OpinionOrderBook]:
        """获取模拟订单簿"""
        import random
        spread = random.uniform(0.01, 0.03)
        return OpinionOrderBook(
            yes_bid=round(max(0.01, self.base_price - spread / 2), 3),
            yes_ask=round(min(0.99, self.base_price + spread / 2), 3),
            yes_bid_size=random.uniform(100, 1000),
            yes_ask_size=random.uniform(100, 1000)
        )

    def get_market_info(self, market_id: str) -> Optional[OpinionMarket]:
        """获取模拟市场信息"""
        yes_price = self.base_price
        return OpinionMarket(
            market_id=str(market_id),
            market_title='Mock Opinion Market',
            yes_token_id=f'0x{"1234567890abcdef" * 2}',
            no_token_id=f'0x{"fedcba987654321" * 2}',
            yes_price=yes_price,
            no_price=1.0 - yes_price,
            volume=1500000.00,
            volume_24h=125000.00,
            status='Activated'
        )

    def search_markets(self, keyword: str, limit: int = 50) -> List[Dict]:
        return self.get_markets(limit=limit)

    def clear_cache(self):
        pass


def create_opinion_client(config: Dict, use_mock: bool = False):
    """
    创建 Opinion 客户端

    Args:
        config: 配置字典
        use_mock: 是否使用模拟客户端

    Returns:
        Opinion 客户端实例
    """
    if use_mock:
        logger.info("使用 Opinion 模拟客户端")
        return MockOpinionClient(config)
    else:
        logger.info("使用 Opinion 真实 API 客户端")
        return OpinionAPIClient(config)
