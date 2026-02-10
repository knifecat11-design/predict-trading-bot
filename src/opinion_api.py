"""
Opinion.trade 市场监测 API（只读模式 + SDK 交易模式）
专注于数据监测，不支持交易
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
    yes_price: float
    no_price: float
    volume: float
    volume_24h: float
    status: str
    yes_token_id: str = ""
    no_token_id: str = ""


@dataclass
class OpinionOrderBook:
    """Opinion 订单簿"""
    yes_bid: float
    yes_ask: float
    yes_bid_size: float
    yes_ask_size: float


class OpinionAPIClient:
    """
    Opinion.trade 市场监测客户端

    两种模式:
    1. 只读模式 (只需要 api_key) - 使用 HTTP 请求
    2. 完整模式 (需要 private_key + multi_sig_addr) - 使用 SDK

    API 特点:
    - Base URL: https://proxy.opinion.trade:8443/openapi
    - 认证: apikey header, Authorization: Bearer, X-API-Key
    - 速率限制: 15 req/s
    """

    def __init__(self, config: Dict):
        self.config = config

        # 获取 API 配置
        opinion_config = config.get('opinion', {})
        self.api_key = opinion_config.get('api_key', '')
        self.base_url = opinion_config.get('base_url', 'https://proxy.opinion.trade:8443')
        self.private_key = opinion_config.get('private_key', '')
        self.multi_sig_addr = opinion_config.get('multi_sig_addr', '')

        # 缓存
        self._markets_cache: List[Dict] = []
        self._cache_time = 0
        self._cache_duration = opinion_config.get('cache_seconds', 30)

        # 初始化客户端
        self._client = None
        self._use_sdk = False

        if not self.api_key:
            logger.warning("未设置 OPINION_API_KEY")
            return

        # 尝试使用 SDK (需要 private_key 和 multi_sig_addr)
        if self.private_key and self.multi_sig_addr:
            try:
                from opinion_clob_sdk import Client as OpinionClient
                from opinion_clob_sdk import TopicStatusFilter, TopicType

                self._client = OpinionClient(
                    host=self.base_url,
                    apikey=self.api_key,
                    chain_id=56,  # BNB Chain
                    rpc_url=opinion_config.get('rpc_url', 'https://bsc-dataseed.binance.org'),
                    private_key=self.private_key,
                    multi_sig_addr=self.multi_sig_addr,
                )
                self.TopicStatusFilter = TopicStatusFilter
                self.TopicType = TopicType
                self._use_sdk = True
                logger.info(f"Opinion API: {self.base_url} (SDK 模式)")
            except ImportError:
                logger.warning("未安装 opinion-clob-sdk，将使用 HTTP 模式")
                self._init_http_client()
            except Exception as e:
                logger.warning(f"SDK 初始化失败: {e}，将使用 HTTP 模式")
                self._init_http_client()
        else:
            # 只读模式：使用 HTTP 请求
            self._init_http_client()

    def _init_http_client(self):
        """初始化 HTTP 客户端（只读模式）"""
        import requests
        self.session = requests.Session()
        # Opinion API 使用小写 'apikey' 头（不是 Authorization 或 X-API-Key）
        self.session.headers.update({
            'apikey': self.api_key,
            'Content-Type': 'application/json'
        })
        self._use_sdk = False
        logger.info(f"Opinion API: {self.base_url} (HTTP 只读模式)")

    def _ensure_client(self):
        """确保客户端已初始化"""
        if not self.api_key:
            raise RuntimeError("Opinion API key 未配置")

        if not self._client and not self.session:
            raise RuntimeError("Opinion 客户端未初始化")

    def get_markets(self,
                    status: str = 'activated',
                    sort_by: int = 5,
                    limit: int = 500) -> List[Dict]:
        """
        获取市场列表（分页抓取全站）
        """
        try:
            self._ensure_client()

            # 检查缓存
            if time.time() - self._cache_time < self._cache_duration and self._markets_cache:
                return self._markets_cache[:limit]

            all_markets = []
            page_size = 20  # Opinion API max per page
            max_pages = (limit + page_size - 1) // page_size

            # SDK 模式
            if self._use_sdk:
                return self._get_markets_sdk(status, limit, page_size, max_pages)
            # HTTP 模式
            else:
                return self._get_markets_http(status, limit, page_size, max_pages)

        except Exception as e:
            logger.error(f"获取 Opinion 市场失败: {e}")
            return self._markets_cache[:limit] if self._markets_cache else []

    def _get_markets_sdk(self, status: str, limit: int, page_size: int, max_pages: int) -> List[Dict]:
        """使用 SDK 获取市场列表"""
        status_filter = self.TopicStatusFilter.ACTIVATED
        if status == 'closed' or status == 'resolved':
            status_filter = self.TopicStatusFilter.RESOLVED
        elif status == 'all':
            status_filter = self.TopicStatusFilter.ALL

        all_markets = []
        for page in range(1, max_pages + 1):
            try:
                response = self._client.get_markets(
                    topic_type=self.TopicType.ALL,
                    status=status_filter,
                    page=page,
                    limit=page_size,
                )

                if hasattr(response, 'errno') and response.errno != 0:
                    logger.error(f"Opinion API 错误: errno={response.errno}")
                    break

                if hasattr(response, 'result') and hasattr(response.result, 'list'):
                    batch = response.result.list
                    if not batch:
                        break

                    for m in batch:
                        market_dict = {
                            'marketId': getattr(m, 'market_id', None),
                            'marketTitle': getattr(m, 'market_title', ''),
                            'yesTokenId': getattr(m, 'yes_token_id', ''),
                            'noTokenId': getattr(m, 'no_token_id', ''),
                            'volume': getattr(m, 'volume', '0'),
                            'volume24h': getattr(m, 'volume24h', '0'),
                            'statusEnum': getattr(m, 'status', ''),
                            'cutoff_at': getattr(m, 'cutoff_at', None),
                            'condition_id': getattr(m, 'condition_id', ''),
                            'chain_id': getattr(m, 'chain_id', 56),
                            'description': getattr(m, 'description', ''),
                            'category': getattr(m, 'category', ''),
                        }
                        all_markets.append(market_dict)

                    if len(batch) < page_size:
                        break
                else:
                    break
            except Exception as e:
                logger.error(f"获取第 {page} 页失败: {e}")
                break

        if all_markets:
            self._markets_cache = all_markets
            self._cache_time = time.time()
            logger.info(f"Opinion (SDK): 获取到 {len(all_markets)} 个市场")

        return all_markets[:limit]

    def _get_markets_http(self, status: str, limit: int, page_size: int, max_pages: int) -> List[Dict]:
        """使用 HTTP 获取市场列表"""
        all_markets = []

        for page in range(max_pages):
            offset = page * page_size
            params = {
                'status': status,
                'sortBy': 5,  # 按 24h 交易量排序
                'limit': page_size,
                'offset': offset,
            }

            try:
                response = self.session.get(
                    f"{self.base_url}/market",
                    params=params,
                    timeout=15
                )

                if response.status_code == 200:
                    data = response.json()
                    # Opinion API 直接返回 result，没有 code 字段
                    result = data.get('result', {})
                    # 如果 result 是空字典但有其他字段，检查是否是错误
                    if not result and data.get('errno') not in [None, 0]:
                        logger.error(f"Opinion API 错误: {data}")
                        break

                    batch = result.get('list', []) if isinstance(result, dict) else []
                    if not batch:
                        break
                    all_markets.extend(batch)
                    if len(batch) < page_size:
                        break
                elif response.status_code == 401:
                    logger.error("Opinion API 认证失败，请检查 API Key")
                    break
                elif response.status_code == 429:
                    logger.warning("Opinion API 速率限制")
                    break
                else:
                    logger.error(f"Opinion API HTTP {response.status_code}")
                    break
            except Exception as e:
                logger.error(f"HTTP 请求失败: {e}")
                break

        if all_markets:
            self._markets_cache = all_markets
            self._cache_time = time.time()
            logger.info(f"Opinion (HTTP): 获取到 {len(all_markets)} 个市场")

        return all_markets[:limit]

    def get_token_price(self, token_id: str) -> Optional[float]:
        """获取 Token 最新价格"""
        try:
            self._ensure_client()

            # SDK 模式
            if self._use_sdk:
                return self._get_price_sdk(token_id)
            # HTTP 模式
            else:
                return self._get_price_http(token_id)

        except Exception as e:
            logger.debug(f"获取 Token 价格失败 {token_id}: {e}")
            return None

    def _get_price_sdk(self, token_id: str) -> Optional[float]:
        """使用 SDK 获取价格"""
        response = self._client.get_orderbook(token_id)

        if hasattr(response, 'errno') and response.errno != 0:
            return None

        if not hasattr(response, 'result'):
            return None

        result = response.result
        bids = getattr(result, 'bids', []) or []
        asks = getattr(result, 'asks', []) or []

        best_bid = float(bids[0].price) if bids else 0.0
        best_ask = float(asks[0].price) if asks else 0.0

        if best_bid > 0 and best_ask > 0:
            return round((best_bid + best_ask) / 2, 4)
        elif best_ask > 0:
            return round(best_ask, 4)
        elif best_bid > 0:
            return round(best_bid, 4)
        return None

    def _get_price_http(self, token_id: str) -> Optional[float]:
        """使用 HTTP 获取价格"""
        params = {'token_id': token_id}
        response = self.session.get(
            f"{self.base_url}/token/latest-price",
            params=params,
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            # Opinion API 直接返回 price 字段，没有 code 包装
            price = data.get('price')
            if price:
                return float(price)

        return None

    def get_order_book(self, token_id: str) -> Optional[OpinionOrderBook]:
        """获取订单簿"""
        try:
            self._ensure_client()

            # SDK 模式
            if self._use_sdk:
                return self._get_orderbook_sdk(token_id)
            # HTTP 模式
            else:
                return self._get_orderbook_http(token_id)

        except Exception as e:
            logger.debug(f"获取订单簿失败 {token_id}: {e}")
            return None

    def _get_orderbook_sdk(self, token_id: str) -> Optional[OpinionOrderBook]:
        """使用 SDK 获取订单簿"""
        response = self._client.get_orderbook(token_id)

        if hasattr(response, 'errno') and response.errno != 0:
            return None

        if not hasattr(response, 'result'):
            return None

        result = response.result
        bids = getattr(result, 'bids', []) or []
        asks = getattr(result, 'asks', []) or []

        yes_bid = float(bids[0].price) if bids else 0.49
        yes_ask = float(asks[0].price) if asks else 0.51
        bid_size = float(bids[0].size) if bids else 100
        ask_size = float(asks[0].size) if asks else 100

        return OpinionOrderBook(
            yes_bid=round(yes_bid, 4),
            yes_ask=round(yes_ask, 4),
            yes_bid_size=bid_size,
            yes_ask_size=ask_size
        )

    def _get_orderbook_http(self, token_id: str) -> Optional[OpinionOrderBook]:
        """使用 HTTP 获取订单簿"""
        params = {'token_id': token_id}
        response = self.session.get(
            f"{self.base_url}/token/orderbook",
            params=params,
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            # Opinion API 直接返回 bids/asks，没有 result 包装
            bids = data.get('bids', [])
            asks = data.get('asks', [])

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
        return None

    def get_market_info(self, market_id: str) -> Optional[OpinionMarket]:
        """获取市场详细信息"""
        try:
            markets = self.get_markets()

            for market in markets:
                if str(market.get('marketId')) == str(market_id):
                    yes_token_id = market.get('yesTokenId', '')
                    yes_price = self.get_token_price(yes_token_id) or 0.5
                    no_price = round(1.0 - yes_price, 4)

                    return OpinionMarket(
                        market_id=str(market['marketId']),
                        market_title=market.get('marketTitle', 'Unknown'),
                        yes_price=yes_price,
                        no_price=no_price,
                        volume=float(market.get('volume', 0) or 0),
                        volume_24h=float(market.get('volume24h', 0) or 0),
                        status=market.get('statusEnum', 'Unknown'),
                        yes_token_id=yes_token_id,
                        no_token_id=market.get('noTokenId', ''),
                    )

        except Exception as e:
            logger.error(f"获取市场信息失败 {market_id}: {e}")

        return None

    def search_markets(self, keyword: str, limit: int = 50) -> List[Dict]:
        """搜索市场"""
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
