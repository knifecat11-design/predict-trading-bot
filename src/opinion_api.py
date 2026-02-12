    def _get_price_http(self, token_id: str) -> Optional[float]:
        """使用 HTTP 获取价格（返回 best ask - 买入价）"""
        params = {'token_id': token_id}
        response = self.session.get(
            f"{self.base_url}/token/orderbook",
            params=params,
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            # Opinion API 统一包装在 result 字段中
            # 从 result 中获取 bids/asks（修复：不要从顶层读取）
            result = data.get('result', {})
            bids = result.get('bids', []) if isinstance(result, dict) else []
            asks = result.get('asks', []) if isinstance(result, dict) else []
            # 返回 best ask（最低卖价）用于买入
            if asks:
                return round(float(asks[0]['price']), 4)

            # Fallback: 如果没有 asks，使用最佳 bid 估算（流动性低时）
            bids = result.get('bids', []) if isinstance(result, dict) else []
            if bids:
                best_bid = float(bids[0]['price'])
                logger.debug(f"Token {token_id} 无 asks，使用 bid 估算: {best_bid}")
                return round(1.0 - best_bid, 4)

            logger.debug(f"Token {token_id} 订单簿为空（无 bids 也无 asks）")
            return None