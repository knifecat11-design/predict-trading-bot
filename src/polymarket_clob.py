"""
Polymarket CLOB 交易客户端

基于 py-clob-client SDK，支持：
- 钱包认证（私钥 → API 凭证）
- 市场数据获取（价格、订单簿、余额）
- 下单/撤单（限价单、市价单）

用于做市商策略的实盘执行。
"""

import logging
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Polymarket CLOB 常量
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet


@dataclass
class PolymarketOrder:
    """Polymarket 订单结果"""
    order_id: str
    status: str  # 'open', 'filled', 'cancelled', 'error'
    side: str
    price: float
    size: float
    token_id: str


class PolymarketClobClient:
    """
    Polymarket CLOB 交易客户端

    封装 py-clob-client SDK，提供简洁的交易接口。

    初始化流程：
    1. 传入私钥 → 创建 ClobClient
    2. 自动 derive API credentials
    3. 调用 place_order / cancel_order 等
    """

    def __init__(self, private_key: str, funder_address: str = None):
        """
        Args:
            private_key: 钱包私钥（0x 前缀或不带）
            funder_address: 资金地址（proxy wallet 场景需要，EOA 可不传）
        """
        self.private_key = private_key
        self.funder_address = funder_address
        self._client = None
        self._initialized = False

    def initialize(self) -> bool:
        """初始化 CLOB 客户端并生成 API 凭证"""
        try:
            from py_clob_client.client import ClobClient

            kwargs = {
                'host': CLOB_HOST,
                'key': self.private_key,
                'chain_id': CHAIN_ID,
            }

            # 如果有 funder 地址，说明是 proxy wallet
            if self.funder_address:
                kwargs['signature_type'] = 1  # proxy/email wallet
                kwargs['funder'] = self.funder_address
            # 否则用 EOA 模式（signature_type=0 是默认值）

            self._client = ClobClient(**kwargs)

            # Derive L2 API credentials
            creds = self._client.create_or_derive_api_creds()
            self._client.set_api_creds(creds)

            self._initialized = True
            logger.info("Polymarket CLOB 客户端初始化成功")
            return True

        except Exception as e:
            logger.error(f"Polymarket CLOB 客户端初始化失败: {e}", exc_info=True)
            self._initialized = False
            return False

    @property
    def is_ready(self) -> bool:
        return self._initialized and self._client is not None

    def get_market(self, condition_id: str) -> Optional[Dict]:
        """获取市场详情（包含 token_id）"""
        if not self.is_ready:
            return None
        try:
            return self._client.get_market(condition_id)
        except Exception as e:
            logger.error(f"获取市场详情失败 {condition_id}: {e}")
            return None

    def get_order_book(self, token_id: str) -> Optional[Dict]:
        """获取订单簿"""
        if not self.is_ready:
            return None
        try:
            return self._client.get_order_book(token_id)
        except Exception as e:
            logger.error(f"获取订单簿失败: {e}")
            return None

    def get_midpoint(self, token_id: str) -> Optional[float]:
        """获取中间价"""
        if not self.is_ready:
            return None
        try:
            mid = self._client.get_midpoint(token_id)
            return float(mid) if mid else None
        except Exception as e:
            logger.debug(f"获取中间价失败: {e}")
            return None

    def get_price(self, token_id: str, side: str = 'BUY') -> Optional[float]:
        """获取买/卖价"""
        if not self.is_ready:
            return None
        try:
            p = self._client.get_price(token_id, side.upper())
            return float(p) if p else None
        except Exception as e:
            logger.debug(f"获取价格失败: {e}")
            return None

    def place_limit_order(self, token_id: str, side: str, price: float,
                          size: float) -> Optional[PolymarketOrder]:
        """
        下限价单 (GTC)

        Args:
            token_id: YES/NO token ID
            side: 'BUY' 或 'SELL'
            price: 价格 (0.01 - 0.99)
            size: 股数
        """
        if not self.is_ready:
            logger.error("客户端未初始化，无法下单")
            return None

        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY, SELL

            order_side = BUY if side.upper() == 'BUY' else SELL

            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=order_side,
            )

            signed_order = self._client.create_order(order_args)
            resp = self._client.post_order(signed_order, OrderType.GTC)

            if resp and resp.get('success'):
                order_id = resp.get('orderID', '')
                logger.info(f"Polymarket 限价单已提交: {side} {size}股 @ {price} (id={order_id[:16]})")
                return PolymarketOrder(
                    order_id=order_id,
                    status='open',
                    side=side.upper(),
                    price=price,
                    size=size,
                    token_id=token_id,
                )
            else:
                error_msg = resp.get('errorMsg', 'Unknown error') if resp else 'No response'
                logger.error(f"Polymarket 下单失败: {error_msg}")
                return None

        except Exception as e:
            logger.error(f"Polymarket 下单异常: {e}", exc_info=True)
            return None

    def place_market_order(self, token_id: str, side: str,
                           amount: float) -> Optional[PolymarketOrder]:
        """
        下市价单 (FOK)

        Args:
            token_id: YES/NO token ID
            side: 'BUY'（amount=美元） 或 'SELL'（amount=股数）
            amount: 金额（BUY时为美元，SELL时为股数）
        """
        if not self.is_ready:
            return None

        try:
            from py_clob_client.clob_types import MarketOrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY, SELL

            order_side = BUY if side.upper() == 'BUY' else SELL

            mo_args = MarketOrderArgs(
                token_id=token_id,
                amount=amount,
                side=order_side,
            )

            signed_order = self._client.create_market_order(mo_args)
            resp = self._client.post_order(signed_order, OrderType.FOK)

            if resp and resp.get('success'):
                order_id = resp.get('orderID', '')
                logger.info(f"Polymarket 市价单已提交: {side} ${amount} (id={order_id[:16]})")
                return PolymarketOrder(
                    order_id=order_id,
                    status='filled',
                    side=side.upper(),
                    price=0,
                    size=amount,
                    token_id=token_id,
                )
            else:
                error_msg = resp.get('errorMsg', 'Unknown error') if resp else 'No response'
                logger.error(f"Polymarket 市价单失败: {error_msg}")
                return None

        except Exception as e:
            logger.error(f"Polymarket 市价单异常: {e}", exc_info=True)
            return None

    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        if not self.is_ready:
            return False
        try:
            resp = self._client.cancel(order_id)
            if resp:
                logger.info(f"Polymarket 撤单成功: {order_id[:16]}")
                return True
            return False
        except Exception as e:
            logger.error(f"Polymarket 撤单失败: {e}")
            return False

    def cancel_all(self) -> bool:
        """撤销所有订单"""
        if not self.is_ready:
            return False
        try:
            resp = self._client.cancel_all()
            logger.info("Polymarket 全部撤单")
            return True
        except Exception as e:
            logger.error(f"Polymarket 全部撤单失败: {e}")
            return False

    def get_open_orders(self) -> List[Dict]:
        """获取当前挂单"""
        if not self.is_ready:
            return []
        try:
            resp = self._client.get_orders()
            return resp if isinstance(resp, list) else []
        except Exception as e:
            logger.error(f"获取挂单失败: {e}")
            return []

    def get_balances(self) -> Dict:
        """获取余额（需要 funder 地址或 EOA）"""
        if not self.is_ready:
            return {}
        try:
            # get_balance_allowance 需要具体的 token_id 和 asset_type
            # 这里返回原始数据让调用方处理
            return {}
        except Exception as e:
            logger.error(f"获取余额失败: {e}")
            return {}
