"""
API 客户端模块
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
class OrderBookEntry:
    """订单簿条目"""
    price: float
    size: float
    orders_count: int


@dataclass
class MarketData:
    """市场数据"""
    market_id: str
    current_price: float
    yes_bid: float      # 买一价
    yes_ask: float      # 卖一价
    best_bid_size: float
    best_ask_size: float
    timestamp: float


@dataclass
class Order:
    """订单信息"""
    order_id: str
    side: str           # 'buy' 或 'sell'
    price: float
    size: float
    status: str         # 'open', 'filled', 'canceled'
    timestamp: float


class PredictAPIClient:
    """
    真实的 Predict.fun API 客户端
    API 文档: https://api.predict.fun/docs
    开发文档: https://dev.predict.fun/
    """

    def __init__(self, config: Dict):
        self.config = config
        self.api_key = config.get('api', {}).get('api_key', '')
        self.base_url = config.get('api', {}).get('base_url', 'https://api.predict.fun')
        self.api_version = 'v1'  # Predict.fun API v1

        # 设置会话
        import requests
        self.session = requests.Session()

        # 设置认证头
        # Predict.fun 使用 x-api-key header，不是 Bearer token
        if self.api_key:
            self.session.headers.update({
                'x-api-key': self.api_key,
                'Content-Type': 'application/json'
            })
        else:
            logger.warning("未设置 PREDICT_API_KEY，某些功能可能无法使用")

        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

        logger.info(f"Predict.fun API 客户端初始化: {self.base_url}")

    def get_markets(self, active_only: bool = True, limit: int = 1000) -> List[Dict]:
        """
        获取市场列表（全站监控，支持分页）

        Args:
            active_only: 是否只返回活跃市场
            limit: 返回数量限制（默认1000，支持全站监控）
        """
        try:
            all_markets = []
            cursor = None
            page_size = 100  # 每页 100 个市场
            max_pages = (limit // page_size) + 1  # 最多获取 limit 个市场

            for page in range(max_pages):
                params = {'limit': page_size}
                if cursor:
                    params['cursor'] = cursor
                if active_only:
                    params['active'] = True

                response = self.session.get(
                    f"{self.base_url}/{self.api_version}/markets",
                    params=params,
                    timeout=15
                )
                response.raise_for_status()
                data = response.json()

                # 检查响应格式
                if isinstance(data, dict):
                    # Predict.fun API 格式: {success: True, cursor: "...", data: [...]}
                    if not data.get('success', True):
                        logger.warning(f"API 返回 success=False")
                        break

                    markets = data.get('data', data.get('items', data.get('markets', [])))
                    cursor = data.get('cursor')

                    if not isinstance(markets, list):
                        logger.warning(f"API 返回了意外的格式: {type(markets)}")
                        break

                    all_markets.extend(markets)
                    logger.debug(f"第 {page + 1} 页: 获取 {len(markets)} 个市场 (总计: {len(all_markets)})")

                    # 检查是否还有更多数据
                    if not cursor or len(markets) == 0:
                        logger.debug(f"已到最后一页 (cursor: {cursor})")
                        break

                    # 检查是否已达到限制
                    if len(all_markets) >= limit:
                        all_markets = all_markets[:limit]
                        break

                elif isinstance(data, list):
                    # 直接返回列表格式
                    all_markets.extend(data)
                    break
                else:
                    logger.warning(f"API 返回了非列表/字典格式: {type(data)}")
                    break

            # 如果需要活跃市场，过滤掉已结算的
            if active_only:
                active_markets = [m for m in all_markets if m.get('status') == 'OPEN']
                logger.info(f"过滤后: {len(active_markets)} 个活跃市场（原始: {len(all_markets)} 个）")
                return active_markets

            logger.info(f"获取到 {len(all_markets)} 个市场")
            return all_markets

        except Exception as e:
            logger.error(f"获取市场列表失败: {e}")
            return []

    def get_market_data(self, market_id: Optional[str] = None) -> Optional[MarketData]:
        """
        获取市场数据（用于套利监控）

        Args:
            market_id: 市场ID（可选，默认使用配置中的市场）
        """
        try:
            if not market_id:
                market_id = self.config.get('market', {}).get('market_id', 'test-market')

            # 获取市场列表
            markets = self.get_markets(active_only=True)

            if not markets:
                # 返回默认值
                return self._get_default_market_data(market_id)

            # 查找指定市场
            target_market = None
            for market in markets:
                # 类型检查：跳过非字典格式的项
                if not isinstance(market, dict):
                    logger.debug(f"跳过非字典格式的市场数据: {type(market)}")
                    continue

                if market.get('id') == market_id or market.get('slug') == market_id:
                    target_market = market
                    break

            # 如果没找到指定市场，使用第一个活跃市场
            if not target_market and markets:
                target_market = markets[0]
                market_id = target_market.get('id', market_id)

            if target_market:
                # 解析市场数据
                current_price = self._parse_price(target_market.get('price', 0.5))

                # 获取订单簿数据
                orderbook = self.get_order_book(market_id)
                yes_bid = orderbook.get('yes_bid', current_price * 0.98)
                yes_ask = orderbook.get('yes_ask', current_price * 1.02)

                return MarketData(
                    market_id=market_id,
                    current_price=current_price,
                    yes_bid=yes_bid,
                    yes_ask=yes_ask,
                    best_bid_size=orderbook.get('bid_size', 100),
                    best_ask_size=orderbook.get('ask_size', 100),
                    timestamp=time.time()
                )

            return self._get_default_market_data(market_id)

        except Exception as e:
            logger.error(f"获取市场数据失败: {e}")
            return self._get_default_market_data(market_id or 'test-market')

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

    def _get_default_market_data(self, market_id: str) -> MarketData:
        """返回默认市场数据"""
        return MarketData(
            market_id=market_id,
            current_price=0.5,
            yes_bid=0.49,
            yes_ask=0.51,
            best_bid_size=100,
            best_ask_size=100,
            timestamp=time.time()
        )

    def get_order_book(self, market_id: str) -> Dict:
        """
        获取订单簿

        Args:
            market_id: 市场ID
        """
        try:
            response = self.session.get(
                f"{self.base_url}/{self.api_version}/markets/{market_id}/orderbook",
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()

                # 解析订单簿数据
                bids = data.get('bids', [])
                asks = data.get('asks', [])

                yes_bid = float(bids[0]['price']) if bids else 0.49
                yes_ask = float(asks[0]['price']) if asks else 0.51

                return {
                    'yes_bid': yes_bid,
                    'yes_ask': yes_ask,
                    'bid_size': float(bids[0]['amount']) if bids else 100,
                    'ask_size': float(asks[0]['amount']) if asks else 100
                }

        except Exception as e:
            logger.debug(f"获取订单簿失败 {market_id}: {e}")

        # 返回默认值
        return {
            'yes_bid': 0.49,
            'yes_ask': 0.51,
            'bid_size': 100,
            'ask_size': 100
        }

    def get_open_orders(self, market_id: Optional[str] = None) -> List[Order]:
        """
        获取当前所有挂单

        Args:
            market_id: 市场ID（可选）
        """
        try:
            if not self.api_key:
                logger.warning("需要 API Key 才能获取订单")
                return []

            params = {'market_id': market_id} if market_id else {}
            response = self.session.get(
                f"{self.base_url}/{self.api_version}/orders",
                params=params,
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            # 处理不同的响应格式
            order_list = []
            if isinstance(data, dict):
                order_list = data.get('items', data.get('data', []))
                if not isinstance(order_list, list):
                    order_list = []
            elif isinstance(data, list):
                order_list = data

            orders = []
            for order_data in order_list:
                # 类型检查
                if not isinstance(order_data, dict):
                    continue

                orders.append(Order(
                    order_id=str(order_data.get('id', '')),
                    side=order_data.get('side', 'buy'),
                    price=float(order_data.get('price', 0)),
                    size=float(order_data.get('amount', 0)),
                    status=order_data.get('status', 'open'),
                    timestamp=time.time()
                ))

            return orders

        except Exception as e:
            logger.error(f"获取订单失败: {e}")
            return []

    def place_order(self, side: str, price: float, size: float,
                    market_id: Optional[str] = None) -> Optional[Order]:
        """
        下单

        Args:
            side: 'buy' 或 'sell'
            price: 价格
            size: 数量
            market_id: 市场ID（可选）
        """
        try:
            if not self.api_key:
                logger.warning("需要 API Key 才能下单")
                return None

            if not market_id:
                market_id = self.config.get('market', {}).get('market_id', 'test-market')

            payload = {
                'market_id': market_id,
                'side': side.lower(),
                'price': price,
                'amount': size,
                'type': 'limit'  # 限价单
            }

            response = self.session.post(
                f"{self.base_url}/{self.api_version}/orders",
                json=payload,
                timeout=15
            )
            response.raise_for_status()
            data = response.json()

            logger.info(f"下单成功: {side} {size} @ {price}")

            return Order(
                order_id=str(data.get('id', '')),
                side=side,
                price=price,
                size=size,
                status='open',
                timestamp=time.time()
            )

        except Exception as e:
            logger.error(f"下单失败: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """
        撤单

        Args:
            order_id: 订单ID
        """
        try:
            if not self.api_key:
                logger.warning("需要 API Key 才能撤单")
                return False

            response = self.session.delete(
                f"{self.base_url}/{self.api_version}/orders/{order_id}",
                timeout=10
            )
            response.raise_for_status()

            logger.info(f"撤单成功: {order_id}")
            return True

        except Exception as e:
            logger.error(f"撤单失败: {e}")
            return False

    def cancel_all_orders(self, market_id: Optional[str] = None) -> int:
        """
        撤销所有挂单

        Args:
            market_id: 市场ID（可选）
        """
        try:
            if not self.api_key:
                logger.warning("需要 API Key 才能撤单")
                return 0

            orders = self.get_open_orders(market_id)
            canceled = 0

            for order in orders:
                if self.cancel_order(order.order_id):
                    canceled += 1

            logger.info(f"撤销了 {canceled} 个订单")
            return canceled

        except Exception as e:
            logger.error(f"批量撤单失败: {e}")
            return 0


class MockAPIClient:
    """
    模拟 API 客户端
    用于测试策略逻辑，等待真实 API 批准后替换
    """

    def __init__(self, config: Dict):
        self.config = config
        self.market_id = config.get('market', {}).get('market_id', 'test-market')
        self.base_price = 0.50  # 模拟基础价格

        # 模拟订单存储
        self._orders: Dict[str, Order] = {}
        self._order_counter = 0

        # 模拟价格波动
        self._price_history = [self.base_price]

    def get_market_data(self) -> MarketData:
        """获取当前市场数据（模拟）"""
        # 模拟价格随机波动 ±2%
        change = random.uniform(-0.02, 0.02)
        self.base_price = max(0.01, min(0.99, self.base_price * (1 + change)))
        self._price_history.append(self.base_price)

        # 模拟买卖价差
        spread = random.uniform(0.01, 0.03)
        yes_bid = round(self.base_price - spread / 2, 3)
        yes_ask = round(self.base_price + spread / 2, 3)

        return MarketData(
            market_id=self.market_id,
            current_price=round(self.base_price, 3),
            yes_bid=max(0.01, yes_bid),
            yes_ask=min(0.99, yes_ask),
            best_bid_size=random.uniform(100, 1000),
            best_ask_size=random.uniform(100, 1000),
            timestamp=time.time()
        )

    def get_markets(self, active_only: bool = True, limit: int = 1000) -> List[Dict]:
        """
        获取市场列表（模拟全站监控）
        生成多个模拟市场用于测试套利逻辑

        Args:
            active_only: 是否只返回活跃市场
            limit: 返回数量限制（最多500个）
        """
        # 模拟市场数据模板（更多市场以匹配 Polymarket 的规模）
        market_templates = [
            # Politics
            {'question': 'Will Trump win 2024 election?', 'base_price': 0.55, 'category': 'politics'},
            {'question': 'Will Biden run for reelection in 2024?', 'base_price': 0.35, 'category': 'politics'},
            {'question': 'Will Republicans control Senate in 2025?', 'base_price': 0.52, 'category': 'politics'},
            {'question': 'Will UK elect Labour government in 2026?', 'base_price': 0.58, 'category': 'politics'},
            {'question': 'Will Ukraine war end in 2026?', 'base_price': 0.55, 'category': 'politics'},
            {'question': 'Will Israel normalize relations with Saudi Arabia?', 'base_price': 0.42, 'category': 'politics'},
            {'question': 'Will China invade Taiwan by 2027?', 'base_price': 0.15, 'category': 'politics'},
            {'question': 'Will Putin remain president in 2026?', 'base_price': 0.75, 'category': 'politics'},
            {'question': 'Will Macron win reelection in France?', 'base_price': 0.45, 'category': 'politics'},
            {'question': 'Will Germany elect CDU chancellor in 2025?', 'base_price': 0.62, 'category': 'politics'},
            {'question': 'Will India reelect Modi in 2029?', 'base_price': 0.68, 'category': 'politics'},
            {'question': 'Will Brazil reelect Lula in 2026?', 'base_price': 0.48, 'category': 'politics'},
            {'question': 'Will North Korea conduct nuclear test in 2026?', 'base_price': 0.38, 'category': 'politics'},
            {'question': 'Will Iran nuclear deal be restored?', 'base_price': 0.22, 'category': 'politics'},
            {'question': 'Will Brexit be reversed by 2030?', 'base_price': 0.18, 'category': 'politics'},

            # Crypto & Finance
            {'question': 'Will Bitcoin reach $100k in 2026?', 'base_price': 0.65, 'category': 'crypto'},
            {'question': 'Will Ethereum surpass $5k in 2026?', 'base_price': 0.45, 'category': 'crypto'},
            {'question': 'Will Bitcoin ETF exceed $50B AUM?', 'base_price': 0.72, 'category': 'crypto'},
            {'question': 'Will Solana reach $500?', 'base_price': 0.42, 'category': 'crypto'},
            {'question': 'Will XRP relist on major US exchanges?', 'base_price': 0.58, 'category': 'crypto'},
            {'question': 'Will Cardano reach $5?', 'base_price': 0.32, 'category': 'crypto'},
            {'question': 'Will Dogecoin reach $1?', 'base_price': 0.25, 'category': 'crypto'},
            {'question': 'Will US recession happen in 2026?', 'base_price': 0.40, 'category': 'finance'},
            {'question': 'Will Fed cut rates below 3%?', 'base_price': 0.55, 'category': 'finance'},
            {'question': 'Will Tesla stock reach $500?', 'base_price': 0.50, 'category': 'finance'},
            {'question': 'Will NVIDIA reach $2000?', 'base_price': 0.68, 'category': 'finance'},
            {'question': 'Will Apple reach $250?', 'base_price': 0.62, 'category': 'finance'},
            {'question': 'Will Amazon acquire a major company?', 'base_price': 0.42, 'category': 'finance'},
            {'question': 'Will Meta stock hit $1000?', 'base_price': 0.48, 'category': 'finance'},
            {'question': 'Will Google split its stock?', 'base_price': 0.35, 'category': 'finance'},
            {'question': 'Will Microsoft acquire OpenAI?', 'base_price': 0.28, 'category': 'finance'},
            {'question': 'Will US ban crypto mining?', 'base_price': 0.12, 'category': 'crypto'},
            {'question': 'Will EU regulate stablecoins?', 'base_price': 0.78, 'category': 'crypto'},
            {'question': 'Will China launch CBDC?', 'base_price': 0.85, 'category': 'crypto'},
            {'question': 'Will DeFi TVL exceed $500B?', 'base_price': 0.45, 'category': 'crypto'},

            # Technology
            {'question': 'Will AI pass Turing test by 2027?', 'base_price': 0.35, 'category': 'tech'},
            {'question': 'Will Google achieve AGI by 2028?', 'base_price': 0.30, 'category': 'tech'},
            {'question': 'Will GPT-5 be released in 2026?', 'base_price': 0.72, 'category': 'tech'},
            {'question': 'Will Apple release AR glasses?', 'base_price': 0.70, 'category': 'tech'},
            {'question': 'Will SpaceX land on Mars by 2030?', 'base_price': 0.25, 'category': 'tech'},
            {'question': 'Will Tesla release Robotaxi in 2026?', 'base_price': 0.55, 'category': 'tech'},
            {'question': 'Will quantum computers break encryption?', 'base_price': 0.20, 'category': 'tech'},
            {'question': 'Will Apple launch foldable iPhone?', 'base_price': 0.52, 'category': 'tech'},
            {'question': 'Will NVIDIA release new GPU architecture?', 'base_price': 0.88, 'category': 'tech'},
            {'question': 'Will Intel regain market share?', 'base_price': 0.38, 'category': 'tech'},
            {'question': 'Will AMD surpass Intel in revenue?', 'base_price': 0.65, 'category': 'tech'},
            {'question': 'Will Samsung release holographic TV?', 'base_price': 0.22, 'category': 'tech'},
            {'question': 'Will 6G launch commercially by 2030?', 'base_price': 0.45, 'category': 'tech'},
            {'question': 'Will flying cars be legal by 2028?', 'base_price': 0.08, 'category': 'tech'},
            {'question': 'Will brain implants become mainstream?', 'base_price': 0.15, 'category': 'tech'},

            # Sports & Entertainment
            {'question': 'Will Saudi Arabia host World Cup?', 'base_price': 0.75, 'category': 'sports'},
            {'question': 'Will Lakers win NBA championship in 2026?', 'base_price': 0.35, 'category': 'sports'},
            {'question': 'Will Messi play in MLS in 2026?', 'base_price': 0.65, 'category': 'sports'},
            {'question': 'Will Olympics be held in LA?', 'base_price': 0.82, 'category': 'sports'},
            {'question': 'Will a movie gross $3B in 2026?', 'base_price': 0.42, 'category': 'entertainment'},
            {'question': 'Will Netflix lose 5M+ subscribers?', 'base_price': 0.28, 'category': 'entertainment'},
            {'question': 'Will Disney+ surpass Netflix?', 'base_price': 0.32, 'category': 'entertainment'},
            {'question': 'Will a video game sell 50M copies?', 'base_price': 0.55, 'category': 'entertainment'},
            {'question': 'Will Taylor Swift tour in 2026?', 'base_price': 0.78, 'category': 'entertainment'},
            {'question': 'Will FIFA ban video technology?', 'base_price': 0.18, 'category': 'sports'},
            {'question': 'Will NFL expand to Europe?', 'base_price': 0.35, 'category': 'sports'},
            {'question': 'Will an eSports athlete earn $10M?', 'base_price': 0.48, 'category': 'sports'},
            {'question': 'Will a VR game win Game of the Year?', 'base_price': 0.52, 'category': 'entertainment'},
            {'question': 'Will a TikTok star win Oscar?', 'base_price': 0.22, 'category': 'entertainment'},
            {'question': 'Will a YouTube video hit 10B views?', 'base_price': 0.38, 'category': 'entertainment'},

            # Science & Environment
            {'question': 'Will global temperature rise exceed 1.5C?', 'base_price': 0.65, 'category': 'science'},
            {'question': 'Will carbon emissions peak by 2026?', 'base_price': 0.42, 'category': 'environment'},
            {'question': 'Will renewable energy exceed 50%?', 'base_price': 0.58, 'category': 'environment'},
            {'question': 'Will China land on moon by 2027?', 'base_price': 0.60, 'category': 'science'},
            {'question': 'Will SpaceX starship succeed in 2026?', 'base_price': 0.72, 'category': 'science'},
            {'question': 'Will James Webb telescope find life?', 'base_price': 0.25, 'category': 'science'},
            {'question': 'Will fusion energy become commercial?', 'base_price': 0.18, 'category': 'science'},
            {'question': 'Will electric vehicles exceed 50% sales?', 'base_price': 0.55, 'category': 'environment'},
            {'question': 'Will lab-grown meat be approved?', 'base_price': 0.48, 'category': 'science'},
            {'question': 'Will a hurricane hit NYC in 2026?', 'base_price': 0.32, 'category': 'environment'},
            {'question': 'Will California have magnitude 7+ quake?', 'base_price': 0.38, 'category': 'science'},
            {'question': 'Will Arctic be ice-free in summer?', 'base_price': 0.45, 'category': 'environment'},
            {'question': 'Will a species go extinct in 2026?', 'base_price': 0.65, 'category': 'environment'},
            {'question': 'Will ocean cleanup remove 1000 tons?', 'base_price': 0.52, 'category': 'environment'},
            {'question': 'Will a new virus emerge in 2026?', 'base_price': 0.42, 'category': 'science'},

            # Business & Economy
            {'question': 'Will Meta layoff 10000+ employees?', 'base_price': 0.35, 'category': 'business'},
            {'question': 'Will Amazon acquire a major company?', 'base_price': 0.42, 'category': 'business'},
            {'question': 'Will US minimum wage reach $15?', 'base_price': 0.58, 'category': 'economy'},
            {'question': 'Will unemployment exceed 8%?', 'base_price': 0.32, 'category': 'economy'},
            {'question': 'Will inflation fall below 2%?', 'base_price': 0.45, 'category': 'economy'},
            {'question': 'Will GDP growth exceed 5%?', 'base_price': 0.38, 'category': 'economy'},
            {'question': 'Will housing prices crash 20%?', 'base_price': 0.25, 'category': 'economy'},
            {'question': 'Will gold reach $3000?', 'base_price': 0.52, 'category': 'finance'},
            {'question': 'Will oil exceed $150?', 'base_price': 0.42, 'category': 'energy'},
            {'question': 'Will a unicorn IPO in 2026?', 'base_price': 0.68, 'category': 'business'},
            {'question': 'Will a bank fail in 2026?', 'base_price': 0.28, 'category': 'finance'},
            {'question': 'Will Bitcoin become legal tender?', 'base_price': 0.22, 'category': 'crypto'},
            {'question': 'Will US national debt exceed $40T?', 'base_price': 0.78, 'category': 'economy'},
            {'question': 'Will China economy grow 6%?', 'base_price': 0.45, 'category': 'economy'},
            {'question': 'Will Eurozone avoid recession?', 'base_price': 0.55, 'category': 'economy'},
        ]

        markets = []
        num_markets = min(len(market_templates), limit, 500)  # 最多500个市场

        for i, template in enumerate(market_templates[:num_markets]):
            # 为每个市场添加一些价格随机性（±5%）
            price_variation = random.uniform(-0.05, 0.05)
            price = max(0.05, min(0.95, template['base_price'] + price_variation))

            # 随机生成交易量
            volume = random.randint(10000, 500000)

            market = {
                'id': f'predict-market-{i+1}',
                'slug': f'predict-market-{i+1}',
                'question': template['question'],
                'price': round(price, 3),
                'active': True,
                'volume': volume,
                'end_date': '2026-12-31T23:59:59Z',
                'category': template.get('category', 'other')
            }
            markets.append(market)

        return markets

    def get_open_orders(self) -> List[Order]:
        """获取当前所有挂单"""
        return list(self._orders.values())

    def place_order(self, side: str, price: float, size: float) -> Order:
        """下单"""
        self._order_counter += 1
        order = Order(
            order_id=f"order_{self._order_counter}",
            side=side,
            price=price,
            size=size,
            status='open',
            timestamp=time.time()
        )
        self._orders[order.order_id] = order
        return order

    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        if order_id in self._orders:
            self._orders[order_id].status = 'canceled'
            del self._orders[order_id]
            return True
        return False

    def cancel_all_orders(self) -> int:
        """撤销所有挂单"""
        count = len(self._orders)
        self._orders.clear()
        return count

    def get_order_book(self, market_id: str) -> Dict:
        """获取订单簿（模拟）"""
        spread = random.uniform(0.01, 0.03)
        return {
            'yes_bid': round(self.base_price - spread / 2, 3),
            'yes_ask': round(self.base_price + spread / 2, 3),
            'bid_size': random.uniform(100, 1000),
            'ask_size': random.uniform(100, 1000)
        }


def create_api_client(config: Dict, use_mock: bool = True):
    """
    创建 API 客户端工厂函数

    Args:
        config: 配置字典
        use_mock: 是否使用模拟客户端（默认True）

    Returns:
        API 客户端实例
    """
    if use_mock:
        logger.info("使用 Predict.fun 模拟客户端")
        return MockAPIClient(config)
    else:
        logger.info("使用 Predict.fun 真实 API 客户端")
        return PredictAPIClient(config)
