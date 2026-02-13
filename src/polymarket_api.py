"""
Polymarket 市场监测 API（简化版）
专注于数据监测，不支持交易
Polymarket Gamma API: https://gamma-api.polymarket.com
"""

import time
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

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
    tags: List[str] = None


@dataclass
class PolymarketOrderBook:
    """Polymarket 订单簿（包含 Yes 和 No 价格）"""
    yes_bid: float
    yes_ask: float
    yes_bid_size: float = 100.0
    yes_ask_size: float = 100.0
    no_bid: float = 0.0
    no_ask: float = 0.0
    no_bid_size: float = 100.0
    no_ask_size: float = 100.0


class PolymarketClient:
    """
    Polymarket 市场监测客户端

    功能：
    - 获取全站市场列表（分页）
    - 按标签筛选市场
    - 搜索市场
    - 获取市场价格

    API: https://gamma-api.polymarket.com/markets
    """

    # 可用标签（来自 Polymarket 官网）
    TAGS = [
        'politics',
        'crypto',
        'sports-live',  # 注意：Polymarket 用 sports-live 而不是 sports/live
        'finance',
        'geopolitics',
        'tech',
        'pop-culture',
        'world',
        'economy'
    ]

    def __init__(self, config: Dict = None):
        self.config = config or {}

        # Gamma API 端点
        self.base_url = "https://gamma-api.polymarket.com"

        # 使用 requests
        import requests
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Origin': 'https://polymarket.com',
            'Referer': 'https://polymarket.com/'
        })

        # 缓存
        self._markets_cache: List[Dict] = []
        self._cache_time = 0
        self._cache_duration = self.config.get('polymarket', {}).get('cache_seconds', 30)

    def get_markets(self,
                    tag: str = None,
                    limit: int = 500,
                    active_only: bool = True,
                    sort_by: str = 'volume') -> List[Dict]:
        """
        获取市场列表

        Args:
            tag: 标签筛选，可选: politics, crypto, sports-live, finance, geopolitics, tech, pop-culture, world, economy
            limit: 返回数量限制
            active_only: 是否只返回活跃市场
            sort_by: 排序方式（默认 volume 按24h交易量排序）

        Returns:
            市场列表
        """
        try:
            # 检查缓存（仅当不使用标签筛选时）
            if tag is None and time.time() - self._cache_time < self._cache_duration and self._markets_cache:
                logger.debug(f"使用缓存 ({len(self._markets_cache)} 个)")
                return self._markets_cache[:limit]

            all_markets = []
            page_size = 100  # Gamma API max per page
            max_pages = (limit + page_size - 1) // page_size

            for page in range(max_pages):
                offset = page * page_size
                params = {
                    'limit': page_size,
                    'offset': offset,
                }

                # 筛选条件
                if active_only:
                    params['active'] = 'true'
                    params['closed'] = 'false'

                # 标签筛选
                if tag:
                    params['tag'] = tag

                response = self.session.get(
                    f"{self.base_url}/markets",
                    params=params,
                    timeout=15
                )

                if response.status_code != 200:
                    logger.error(f"Polymarket API 错误: HTTP {response.status_code}")
                    break

                batch = response.json()

                if not batch:
                    break

                all_markets.extend(batch)

                if len(batch) < page_size:
                    break  # 最后一页

            # 按交易量排序（24h Volume）
            if sort_by == 'volume':
                all_markets.sort(
                    key=lambda m: float(m.get('volume24hr', 0) or 0),
                    reverse=True
                )

            # 更新缓存（仅全量数据）
            if tag is None:
                self._markets_cache = all_markets
                self._cache_time = time.time()

            tag_str = f"[{tag}]" if tag else ""
            logger.info(f"Polymarket{tag_str}: 获取到 {len(all_markets)} 个市场")

            return all_markets[:limit]

        except Exception as e:
            logger.error(f"获取 Polymarket 市场失败: {e}")
            return self._markets_cache[:limit] if self._markets_cache else []

    def get_all_tags_markets(self, limit_per_tag: int = 100) -> List[Dict]:
        """
        获取所有标签的市场（覆盖全站）

        Args:
            limit_per_tag: 每个标签返回的市场数量

        Returns:
            所有市场（去重后按24h交易量排序）
        """
        all_markets = []
        seen_ids = set()

        for tag in self.TAGS:
            try:
                markets = self.get_markets(tag=tag, limit=limit_per_tag, active_only=True)

                for m in markets:
                    condition_id = m.get('conditionId') or m.get('condition_id')
                    if condition_id and condition_id not in seen_ids:
                        seen_ids.add(condition_id)
                        all_markets.append(m)

                logger.info(f"Tag '{tag}': {len(markets)} 个市场")

            except Exception as e:
                logger.warning(f"获取标签 '{tag}' 失败: {e}")
                continue

        # 按交易量排序
        all_markets.sort(
            key=lambda m: float(m.get('volume24hr', 0) or 0),
            reverse=True
        )

        logger.info(f"全站总计: {len(all_markets)} 个市场（去重后）")
        return all_markets

    def get_market_price(self, condition_id: str) -> Optional[float]:
        """
        获取市场价格

        Args:
            condition_id: 市场 ID

        Returns:
            Yes 价格（0-1）
        """
        try:
            import json

            markets = self.get_markets(limit=1000, active_only=True)

            for market in markets:
                cid = market.get('conditionId') or market.get('condition_id')
                if cid == condition_id:
                    # 优先使用 outcomePrices
                    outcome_prices_str = market.get('outcomePrices', '[]')
                    if isinstance(outcome_prices_str, str):
                        outcome_prices = json.loads(outcome_prices_str)
                        if outcome_prices and len(outcome_prices) >= 1:
                            return float(outcome_prices[0])

                    # 回退：使用 price 字段
                    price = market.get('price', market.get('lastPrice', 0.5))
                    return float(price)

            return None

        except Exception as e:
            logger.debug(f"获取价格失败 {condition_id}: {e}")
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
            markets = self.get_markets(limit=1000, active_only=True)

            if not keyword:
                return markets[:limit]

            keyword_lower = keyword.lower()
            results = []

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

            logger.info(f"搜索 '{keyword}': 找到 {len(results)} 个结果")
            return results

        except Exception as e:
            logger.error(f"搜索市场失败: {e}")
            return []

    def get_order_book(self, condition_id: str) -> Optional[PolymarketOrderBook]:
        """
        获取订单簿数据（用于套利监控）
        包含 Yes 和 No 的 best ask/bid 价格

        Args:
            condition_id: 市场 ID

        Returns:
            PolymarketOrderBook (包含 yes_bid, yes_ask, no_bid, no_ask) 或 None
        """
        try:
            import json

            # 从市场列表中获取最新数据（包含 bestBid/bestAsk 和 noBid/noAsk）
            markets = self.get_markets(limit=1000, active_only=True)

            for market in markets:
                cid = market.get('conditionId') or market.get('condition_id')

                if cid == condition_id:
                    # Yes 价格（bestBid/bestAsk）
                    yes_bid = market.get('bestBid')
                    yes_ask = market.get('bestAsk')

                    # No 价格（noBid/noAsk）
                    no_bid = market.get('noBid')
                    no_ask = market.get('noAsk')

                    # 必须有 Yes 和 No 的 ask 价格
                    if yes_ask is not None and no_ask is not None:
                        return PolymarketOrderBook(
                            yes_bid=round(float(yes_bid), 4) if yes_bid is not None else 0.0,
                            yes_ask=round(float(yes_ask), 4),
                            yes_bid_size=100.0,
                            yes_ask_size=100.0,
                            no_bid=round(float(no_bid), 4) if no_bid is not None else 0.0,
                            no_ask=round(float(no_ask), 4),
                            no_bid_size=100.0,
                            no_ask_size=100.0
                        )

                    # 如果没有 noAsk，跳过这个市场（不使用计算值）
                    logger.debug(f"市场 {condition_id} No 价格不可用，跳过")
                    return None

            # 如果找不到市场，返回 None
            logger.warning(f"未找到市场 {condition_id}")
            return None

        except Exception as e:
            logger.error(f"获取订单簿失败 {condition_id}: {e}")
            return None

    def clear_cache(self):
        """清除缓存"""
        self._markets_cache.clear()
        self._cache_time = 0
        logger.info("Polymarket 缓存已清除")


# 向后兼容的别名
RealPolymarketClient = PolymarketClient


def create_polymarket_client(config: Dict, use_real: bool = True):
    """
    创建 Polymarket 客户端（向后兼容）

    Args:
        config: 配置字典
        use_real: 必须为 True（简化版不再支持模拟模式）
    """
    return PolymarketClient(config)
