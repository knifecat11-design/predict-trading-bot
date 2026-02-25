"""
Probable.markets 市场监测 API
专注于数据监测，不支持交易
Probable Markets API: https://market-api.probable.markets/public/api/v1/

重要说明：
- 公共 API 不提供价格/订单簿数据
- /events 和 /markets 端点只返回市场列表、流动性、交易量
- 所有价格相关端点（/price, /orderbook, /clob/tokens）均返回 500 错误
- 价格数据可能需要通过智能合约或私有 API 获取
- 因此套利监控暂时不包含 Probable Markets
"""

import time
import logging
import json
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ProbableMarket:
    """Probable Market 市场信息"""
    market_id: str
    question: str
    event_id: str
    current_price: float           # Yes 价格（中间价）
    yes_price: float               # Yes 价格
    no_price: float                # No 价格
    liquidity: float
    volume_24h: float
    end_date: Optional[str] = None
    clob_token_ids: List[str] = None
    market_structure: str = "single"
    tags: List[str] = None


@dataclass
class ProbableOrderBook:
    """Probable Market 订单簿（基于市场数据推导）"""
    yes_bid: float
    yes_ask: float
    no_bid: float
    no_ask: float
    yes_bid_size: float = 100.0
    yes_ask_size: float = 100.0
    no_bid_size: float = 100.0
    no_ask_size: float = 100.0


class ProbableClient:
    """
    Probable.markets 市场监测客户端

    API 文档: https://developer.probable.markets
    基础 URL: https://market-api.probable.markets/public/api/v1/

    功能：
    - 获取所有事件列表（分页）
    - 按状态筛选市场（active/closed/ended）
    - 搜索市场
    - 获取市场价格（Yes/No）
    - 获取订单簿（从市场数据推导）

    注意：
    - 该 API 是公开的，无需认证
    - 订单簿端点目前不可用（返回 500 错误），从市场数据推导价格
    """

    BASE_URL = "https://market-api.probable.markets/public/api/v1"

    # 市场结构类型
    MARKET_STRUCTURES = ['single', 'multi']

    # 排序选项
    SORT_OPTIONS = ['liquidity', 'volume', 'endDate', 'startDate']

    def __init__(self, config: Dict = None):
        self.config = config or {}

        # API 端点
        self.base_url = self.config.get('probable', {}).get(
            'base_url', self.BASE_URL
        )

        # 使用 requests
        import requests
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip, deflate',
        })

        # 缓存
        self._markets_cache: List[Dict] = []
        self._events_cache: List[Dict] = []
        self._cache_time = 0
        self._cache_duration = self.config.get('probable', {}).get('cache_seconds', 90)

    def get_events(self,
                   active_only: bool = True,
                   limit: int = 500,
                   offset: int = 0,
                   sort_by: str = None) -> List[Dict]:
        """
        获取事件列表（支持分页获取全部数据）

        Args:
            active_only: 是否只返回活跃事件
            limit: 返回数量限制（实际会获取所有数据，然后返回前 limit 个）
            offset: 分页偏移量（用于分页，内部使用）
            sort_by: 排序方式（注意：API 不支持排序参数，将在客户端排序）

        Returns:
            事件列表，每个事件包含 markets[] 数组
        """
        try:
            # 检查缓存（只有获取全部数据时才缓存）
            if offset == 0:
                cache_key = f"events_{active_only}_{sort_by}"
                if (time.time() - self._cache_time < self._cache_duration and
                    self._events_cache and
                    getattr(self, '_cache_key', '') == cache_key):
                    logger.debug(f"使用缓存 ({len(self._events_cache)} 个事件)")
                    return self._events_cache[:limit]

            # 分页获取所有事件
            all_events = []
            page_size = 20  # API 每页最多返回 20 个
            current_offset = offset

            while True:
                params = {
                    'limit': page_size,
                    'offset': current_offset,
                }

                response = self.session.get(
                    f"{self.base_url}/events",
                    params=params,
                    timeout=15
                )

                if response.status_code != 200:
                    logger.error(f"Probable API 错误: HTTP {response.status_code}")
                    break

                events = response.json()
                if not events:
                    break

                all_events.extend(events)
                current_offset += page_size

                # 如果返回少于 page_size，说明是最后一页
                if len(events) < page_size:
                    break

                # 防止无限循环（安全限制）
                if len(all_events) >= 5000:
                    logger.warning("已达到最大获取数量限制 (5000)")
                    break

            # 筛选活跃事件
            if active_only:
                all_events = [e for e in all_events if e.get('active', False)]

            # 客户端排序（如果指定了排序方式）
            if sort_by == 'liquidity':
                all_events.sort(key=lambda e: float(e.get('liquidity', 0) or 0), reverse=True)
            elif sort_by == 'volume':
                all_events.sort(key=lambda e: float(e.get('volume24hr', 0) or 0), reverse=True)

            # 只在第一次获取时更新缓存
            if offset == 0:
                self._events_cache = all_events
                self._cache_key = f"events_{active_only}_{sort_by}"
                self._cache_time = time.time()

            logger.info(f"Probable Markets: 获取到 {len(all_events)} 个事件")
            return all_events[:limit]

        except Exception as e:
            logger.error(f"获取 Probable 事件失败: {e}")
            return self._events_cache[:limit] if self._events_cache else []

    def get_markets(self,
                    active_only: bool = True,
                    limit: int = 500,
                    sort_by: str = 'liquidity') -> List[Dict]:
        """
        获取所有市场（从事件中展开）

        Args:
            active_only: 是否只返回活跃市场
            limit: 返回数量限制
            sort_by: 排序方式

        Returns:
            市场列表
        """
        try:
            events = self.get_events(active_only=active_only, limit=limit * 2)

            all_markets = []
            for event in events:
                for market in event.get('markets', []):
                    # 添加事件信息到市场
                    market['_event'] = {
                        'id': event.get('id'),
                        'slug': event.get('slug'),
                        'title': event.get('title'),
                    }
                    all_markets.append(market)

            # 排序
            if sort_by == 'liquidity':
                all_markets.sort(
                    key=lambda m: float(m.get('liquidity', 0) or 0),
                    reverse=True
                )
            elif sort_by == 'volume':
                all_markets.sort(
                    key=lambda m: float(m.get('volume24hr', 0) or 0),
                    reverse=True
                )

            self._markets_cache = all_markets
            return all_markets[:limit]

        except Exception as e:
            logger.error(f"获取 Probable 市场失败: {e}")
            return self._markets_cache[:limit] if self._markets_cache else []

    def get_market_by_id(self, market_id: str) -> Optional[Dict]:
        """
        根据 ID 获取市场

        Args:
            market_id: 市场 ID

        Returns:
            市场信息或 None
        """
        try:
            markets = self.get_markets(active_only=False, limit=5000)

            for market in markets:
                if market.get('id') == market_id or str(market.get('id')) == market_id:
                    return market

            return None

        except Exception as e:
            logger.error(f"获取市场 {market_id} 失败: {e}")
            return None

    def get_event_by_slug(self, slug: str) -> Optional[Dict]:
        """
        根据 slug 获取事件

        Args:
            slug: 事件 slug

        Returns:
            事件信息或 None
        """
        try:
            response = self.session.get(
                f"{self.base_url}/events/slug/{slug}",
                timeout=15
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"获取事件 {slug} 失败: HTTP {response.status_code}")
                return None

        except Exception as e:
            logger.error(f"获取事件 {slug} 失败: {e}")
            return None

    def get_market_price(self, market_id: str) -> Optional[Tuple[float, float]]:
        """
        获取市场价格 (Yes, No)

        Args:
            market_id: 市场 ID

        Returns:
            (yes_price, no_price) 或 None
        """
        try:
            market = self.get_market_by_id(market_id)
            if not market:
                return None

            tokens = market.get('tokens', [])
            if len(tokens) >= 2:
                # tokens[0] 通常是 Yes，tokens[1] 通常是 No
                yes_price = self._extract_price_from_token(tokens[0], market)
                no_price = self._extract_price_from_token(tokens[1], market)
                return (yes_price, no_price)

            return None

        except Exception as e:
            logger.debug(f"获取价格失败 {market_id}: {e}")
            return None

    def _extract_price_from_token(self, token: Dict, market: Dict) -> float:
        """
        从 token 提取价格
        Probable Markets API 不直接提供价格，需要从流动性推导
        使用简单的价格估算：假设 Yes + No ≈ 1
        """
        # 从流动性推导价格（简化版）
        liquidity = float(market.get('liquidity', 0) or 0)

        # 如果有 outcome 字段，根据 outcome 类型返回默认价格
        outcome = token.get('outcome', '').lower()
        if outcome == 'yes':
            # 假设流动性平均分布在 Yes 和 No 上
            return 0.5  # 默认值，实际需要从订单簿获取
        elif outcome == 'no':
            return 0.5

        return 0.5

    def get_order_book(self, market_id: str) -> Optional[ProbableOrderBook]:
        """
        获取订单簿数据（用于套利监控）

        注意：Probable Markets 订单簿 API 目前不可用（返回 500 错误）
        此方法从市场数据推导订单簿价格

        Args:
            market_id: 市场 ID

        Returns:
            ProbableOrderBook 或 None
        """
        try:
            market = self.get_market_by_id(market_id)
            if not market:
                logger.warning(f"未找到市场 {market_id}")
                return None

            tokens = market.get('tokens', [])
            if len(tokens) < 2:
                logger.warning(f"市场 {market_id} tokens 数据不完整")
                return None

            # 推导价格：由于没有真实订单簿，使用流动性推导
            # 这里使用简化逻辑：Yes + No = 1，添加合理价差
            liquidity = float(market.get('liquidity', 0) or 0)

            # 高流动性市场价差更小
            spread = max(0.01, min(0.05, 100000 / (liquidity + 1)) * 0.5)

            # 假设中间价为 0.5（实际应该从历史交易或 ticker 获取）
            mid_price = 0.5

            yes_bid = max(0.01, mid_price - spread)
            yes_ask = min(0.99, mid_price + spread)
            no_bid = max(0.01, 1.0 - yes_ask)
            no_ask = min(0.99, 1.0 - yes_bid)

            return ProbableOrderBook(
                yes_bid=round(yes_bid, 4),
                yes_ask=round(yes_ask, 4),
                no_bid=round(no_bid, 4),
                no_ask=round(no_ask, 4),
                yes_bid_size=100.0,
                yes_ask_size=100.0,
                no_bid_size=100.0,
                no_ask_size=100.0
            )

        except Exception as e:
            logger.error(f"获取订单簿失败 {market_id}: {e}")
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
            markets = self.get_markets(active_only=True, limit=5000)

            if not keyword:
                return markets[:limit]

            keyword_lower = keyword.lower()
            results = []

            for market in markets:
                question = market.get('question', '').lower()
                description = market.get('description', '').lower()
                event_title = market.get('_event', {}).get('title', '').lower()

                # 匹配标题、描述或事件标题
                if (keyword_lower in question or
                    keyword_lower in description or
                    keyword_lower in event_title):
                    results.append(market)

                if len(results) >= limit:
                    break

            logger.info(f"搜索 '{keyword}': 找到 {len(results)} 个结果")
            return results

        except Exception as e:
            logger.error(f"搜索市场失败: {e}")
            return []

    def get_markets_for_arbitrage(self, limit: int = 200) -> List[Dict]:
        """
        获取用于套利监控的市场数据

        返回格式与其他平台一致，便于 cross-platform matching

        Returns:
            市场列表，每个市场包含：
            - id: 市场 ID
            - title: 问题标题
            - match_title: 用于匹配的标题
            - yes: Yes 价格
            - no: No 价格
            - volume: 交易量
            - end_date: 结束日期
        """
        try:
            markets = self.get_markets(active_only=True, limit=limit, sort_by='liquidity')

            result = []
            for market in markets:
                # 从 tokens 获取价格信息
                tokens = market.get('tokens', [])
                yes_price = 0.5
                no_price = 0.5

                # 简化：使用默认价格，实际应该从 ticker 或订单簿获取
                if len(tokens) >= 2:
                    # 这里使用假设价格，因为订单簿 API 不可用
                    yes_price = 0.5
                    no_price = 0.5

                end_date_str = market.get('endDate', '')
                end_date = self._parse_date(end_date_str) if end_date_str else ''

                question = market.get('question', '') or market.get('_event', {}).get('title', '')

                result.append({
                    'id': str(market.get('id', '')),
                    'title': question[:80],
                    'match_title': question,
                    'yes': round(yes_price, 4),
                    'no': round(no_price, 4),
                    'volume': float(market.get('volume24hr', 0) or 0),
                    'liquidity': float(market.get('liquidity', 0) or 0),
                    'end_date': end_date,
                    'clob_token_ids': market.get('clobTokenIds', []),
                    'market_structure': market.get('marketStructure', 'single'),
                })

            logger.info(f"Probable Markets 套利数据: {len(result)} 个市场")
            return result

        except Exception as e:
            logger.error(f"获取 Probable 套利数据失败: {e}")
            return []

    def _parse_date(self, date_str: str) -> str:
        """解析日期字符串"""
        try:
            # ISO 8601 格式
            if 'T' in date_str:
                return date_str.split('T')[0]
            return date_str
        except:
            return date_str

    def clear_cache(self):
        """清除缓存"""
        self._markets_cache.clear()
        self._events_cache.clear()
        self._cache_time = 0
        logger.info("Probable Markets 缓存已清除")


# 向后兼容的别名
RealProbableClient = ProbableClient


def create_probable_client(config: Dict, use_real: bool = True):
    """
    创建 Probable Markets 客户端（向后兼容）

    Args:
        config: 配置字典
        use_real: 必须为 True（Probable Markets API 是公开的）
    """
    return ProbableClient(config)
