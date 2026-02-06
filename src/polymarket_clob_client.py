"""
Polymarket CLOB Client - 基于成熟项目的实现
使用正确的 API 端点获取实时数据
"""

import asyncio
import aiohttp
import json
import logging
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class PolymarketCLOBClient:
    """
    Polymarket CLOB API 客户端
    使用 https://clob.polymarket.com 端点
    """

    BASE_URL = "https://clob.polymarket.com"
    GAMMA_URL = "https://gamma-api.polymarket.com"

    def __init__(self):
        self.session = None
        self.markets_cache = {}

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=30)
        self.session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def get_markets(self, limit: int = 100) -> List[Dict]:
        """获取活跃市场"""
        try:
            url = f"{self.GAMMA_URL}/markets"
            params = {
                'closed': 'false',
                'active': 'true',
                'limit': limit
            }

            async with self.session.get(url, params=params) as resp:
                if resp.status == 200:
                    markets = await resp.json()
                    logger.info(f"[OK] 获取 {len(markets)} 个活跃市场")

                    # 缓存市场
                    for market in markets:
                        condition_id = market.get('conditionId') or market.get('condition_id')
                        if condition_id:
                            self.markets_cache[condition_id] = market

                    return markets
                else:
                    logger.warning(f"获取市场失败: {resp.status}")
                    return []
        except Exception as e:
            logger.error(f"获取市场错误: {e}")
            return []

    async def get_orderbook(self, token_id: str) -> Optional[Dict]:
        """
        获取订单簿 - 使用正确的 /book 端点
        """
        try:
            url = f"{self.BASE_URL}/book"
            params = {'token_id': token_id}

            async with self.session.get(url, params=params) as resp:
                if resp.status == 200:
                    book = await resp.json()
                    return book
                else:
                    logger.warning(f"获取订单簿失败 {token_id}: {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"获取订单簿错误 {token_id}: {e}")
            return None

    async def get_market_orderbooks(self, market: Dict) -> Optional[Dict]:
        """
        获取市场的完整订单簿（Yes 和 No）
        """
        try:
            # 获取 token IDs
            token_ids_str = market.get('clobTokenIds')
            if not token_ids_str:
                return None

            if isinstance(token_ids_str, str):
                token_ids = json.loads(token_ids_str)
            else:
                token_ids = token_ids_str

            if len(token_ids) < 2:
                return None

            yes_token_id = token_ids[0]
            no_token_id = token_ids[1]

            # 并发获取 Yes 和 No 的订单簿
            yes_book, no_book = await asyncio.gather(
                self.get_orderbook(yes_token_id),
                self.get_orderbook(no_token_id),
                return_exceptions=True
            )

            # 处理异常
            if isinstance(yes_book, Exception):
                logger.warning(f"Yes token 订单簿获取失败: {yes_book}")
                yes_book = None
            if isinstance(no_book, Exception):
                logger.warning(f"No token 订单簿获取失败: {no_book}")
                no_book = None

            if not yes_book or not no_book:
                return None

            # 提取最佳买卖价
            yes_best_bid = float(yes_book.get('bids', [{}])[0].get('price', 0)) if yes_book.get('bids') else 0
            yes_best_ask = float(yes_book.get('asks', [{}])[0].get('price', 0)) if yes_book.get('asks') else 0

            no_best_bid = float(no_book.get('bids', [{}])[0].get('price', 0)) if no_book.get('bids') else 0
            no_best_ask = float(no_book.get('asks', [{}])[0].get('price', 0)) if no_book.get('asks') else 0

            # 计算流动性
            yes_liquidity = sum(float(ask.get('size', 0)) for ask in yes_book.get('asks', [])[:5])
            no_liquidity = sum(float(ask.get('size', 0)) for ask in no_book.get('asks', [])[:5])

            return {
                'yes_bid': yes_best_bid,
                'yes_ask': yes_best_ask,
                'no_bid': no_best_bid,
                'no_ask': no_best_ask,
                'yes_liquidity': yes_liquidity,
                'no_liquidity': no_liquidity,
                'timestamp': datetime.now().isoformat()
            }

        except Exception as e:
            logger.error(f"获取市场订单簿错误: {e}")
            return None

    def get_sync_markets(self, limit: int = 100) -> List[Dict]:
        """同步版本的获取市场"""
        return asyncio.run(self.get_markets(limit))

    def get_sync_orderbook(self, token_id: str) -> Optional[Dict]:
        """同步版本的获取订单簿"""
        return asyncio.run(self.get_orderbook(token_id))


def test_connection():
    """测试连接"""
    async def run_test():
        async with PolymarketCLOBClient() as client:
            # 获取市场
            markets = await client.get_markets(limit=5)
            print(f"[OK] 获取 {len(markets)} 个市场")

            if markets:
                m = markets[0]
                print(f"\n示例市场: {m.get('question', 'N/A')[:60]}")
                print(f"outcomePrices: {m.get('outcomePrices', 'N/A')}")

                # 获取订单簿
                orderbook = await client.get_market_orderbooks(m)
                if orderbook:
                    print(f"\n[OK] 订单簿数据:")
                    print(f"  Yes 买一: {orderbook['yes_bid']:.4f}")
                    print(f"  Yes 卖一: {orderbook['yes_ask']:.4f}")
                    print(f"  No 买一: {orderbook['no_bid']:.4f}")
                    print(f"  No 卖一: {orderbook['no_ask']:.4f}")
                    print(f"  Yes 流动性: {orderbook['yes_liquidity']:.2f}")
                    print(f"  No 流动性: {orderbook['no_liquidity']:.2f}")

                    # 计算套利机会
                    yes_ask = orderbook['yes_ask']
                    no_ask = orderbook['no_ask']
                    if yes_ask > 0 and no_ask > 0:
                        # 注意：No token 的价格需要转换为 Yes 的价格
                        # No price = 1 - Yes price
                        no_as_yes = 1 - no_ask
                        combined = yes_ask + no_as_yes
                        arbitrage = (1.0 - combined) * 100

                        print(f"\n套利分析:")
                        print(f"  Yes 价格: {yes_ask:.4f} ({yes_ask*100:.2f}¢)")
                        print(f"  No 价格(换算): {no_as_yes:.4f} ({no_as_yes*100:.2f}¢)")
                        print(f"  组合价格: {combined:.4f} ({combined*100:.2f}¢)")
                        print(f"  套利空间: {arbitrage:.2f}%")

                        if arbitrage > 0:
                            print(f"  [YES] 存在套利机会！")
                        else:
                            print(f"  [NO] 无套利机会")

    asyncio.run(run_test())


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    test_connection()
