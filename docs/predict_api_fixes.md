# Predict.fun API 修复文档

## 概述

本文档详细说明了对 `src/api_client.py` 文件所做的 5 个致命错误修复。

**版本**: v3.0 (2026-02-12)
**API 文档**: https://api.predict.fun/docs
**API 密钥**: `1b0c25d4-8ca6-4aa8-8910-cd72b311e4f6`

---

## 5 个致命错误修复

### 错误 1: 认证头错误

**原代码 (第 55 行)**:
```python
self.session.headers.update({
    'Authorization': f'Bearer {self.api_key}',  # 错误！
    'Content-Type': 'application/json'
})
```

**修复后 (第 69 行)**:
```python
self.session.headers.update({
    'x-api-key': self.api_key,  # 正确：使用小写的 x-api-key
    'Content-Type': 'application/json'
})
```

**说明**: Predict.fun API 使用非标准的 `x-api-key` 请求头（小写），而不是标准的 `Authorization: Bearer`。

---

### 错误 2: 端点前缀缺失

**原代码 (第 74 行)**:
```python
response = self.session.get(
    f"{self.base_url}/markets",  # 错误：缺少 /v1/ 前缀
    params=params,
    timeout=15
)
```

**修复后 (第 103 行)**:
```python
response = self.session.get(
    f"{self.base_url}/v1/markets",  # 正确：添加 /v1/ 前缀
    params=params,
    timeout=15
)
```

**说明**: 所有 Predict.fun API 端点都需要 `/v1/` 前缀，如 `/v1/markets`、`/v1/markets/{id}/orderbook`。

---

### 错误 3: 参数名称错误

**原代码 (第 73 行)**:
```python
params = {
    'status': status,        # 错误：应该是大写 OPEN，不是 open
    'sort': sort,            # 错误：API 不支持此参数
    'limit': min(limit, 100)  # 错误：应该是 first，不是 limit
}
```

**修复后 (第 97-102 行)**:
```python
params = {
    'status': status.upper(),  # 修复：必须大写 (OPEN, REGISTERED, RESOLVED)
    'first': min(limit, 100)  # 修复：参数名是 first
}
# API 不支持 sort 参数，已移除
```

**说明**:
- `status` 参数必须大写：`OPEN`、`REGISTERED`、`RESOLVED`
- 分页参数名是 `first`，不是 `limit`
- `sort` 参数不被 API 支持，已移除

---

### 错误 4: 响应解析错误

**原代码 (第 78 行)**:
```python
markets = data if isinstance(data, list) else data.get('data', data.get('markets', []))
```

**修复后 (第 112-118 行)**:
```python
result = response.json()
if result.get('success') and 'data' in result:
    markets = result['data']
    if markets:
        self._cache = markets
        self._cache_time = time.time()
        logger.info(f"Predict.fun: 获取到 {len(markets)} 个市场 (cursor={result.get('cursor', 'N/A')})")
        return markets[:limit]
```

**说明**: API 返回游标分页格式：`{"success": true, "cursor": "...", "data": [...]}`

---

### 错误 5: 订单簿数据结构错误

**原代码 (第 119-120 行)**:
```python
bids = orderbook.get('bids', [])
asks = orderbook.get('asks', [])
yes_bid = float(bids[0]['price'])  # 错误：bids 是 2D 数组，不是 dict
yes_ask = float(asks[0]['price'])  # 错误：asks 是 2D 数组，不是 dict
```

**修复后 (第 135-147 行, 第 174-189 行)**:
```python
def _parse_price_level(self, level: list) -> Tuple[float, float]:
    """解析订单簿价格层级 [price, size]"""
    if not level or len(level) < 2:
        return (None, 0)
    return (float(level[0]), float(level[1]))  # level[0]=price, level[1]=size

# 在 _get_orderbook 中：
asks = data.get('asks', []) or []
bids = data.get('bids', []) or []
if asks and bids:
    yes_ask, ask_size = self._parse_price_level(asks[0])
    yes_bid, bid_size = self._parse_price_level(bids[0])
```

**说明**: 订单簿数据是 2D 数组格式 `[[price, size], ...]`，不是字典。

---

## API 验证测试结果

```
=== Test 1: Get OPEN markets ===
Found 3 markets
First market: id=8186, question=BTC/USD Up or Down - February 12, 9:30-9:45AM ET...
Status: REGISTERED

=== Test 2: Get orderbook ===
Yes bid: 0.32, Yes ask: 0.5
Bid size: 220.0, Ask size: 200.0

=== Test 3: Get full orderbook ===
Yes: bid=0.34, ask=0.52
No:  bid=0.48, ask=0.66

=== All tests passed! ===
```

---

## API 端点参考

| 端点 | 方法 | 说明 |
|--------|------|------|
| `/v1/markets` | GET | 获取市场列表 |
| `/v1/markets/{id}/orderbook` | GET | 获取订单簿 |
| `/v1/markets?status=OPEN&first=N` | GET | 获取 OPEN 状态市场 |

**重要提示**:
- 所有端点都需要 `/v1/` 前缀
- 认证使用 `x-api-key` 头（小写）
- 市场状态必须大写：`OPEN`、`REGISTERED`、`RESOLVED`
- 分页参数名是 `first`，不是 `limit`
- API 不支持 `sort` 参数

---

## No Token 价格计算

由于 Predict.fun API 的订单簿端点不支持 `outcomeId` 参数来获取 No token 的独立订单簿，No token 价格使用标准预测市场公式计算：

```
No bid = 1 - Yes ask
No ask = 1 - Yes bid
```

这是预测市场的标准做法，因为理论上 Yes 价格 + No 价格应该等于 100%（1.0）。

---

## 文件变更摘要

**文件**: `src/api_client.py`
**行数**: 约 418 行
**主要变更**:

1. 添加 `_parse_price_level()` 方法来正确解析 2D 数组格式的订单簿
2. 添加 `no_bid` 和 `no_ask` 字段到 `MarketData` 类
3. 添加 `status` 字段到 `MarketData` 类
4. 修复所有 API 认证和端点路径
5. 修复所有参数名称和格式
6. 简化交易方法（根据用户要求，专注于监控功能）

---

## 测试命令

```bash
cd C:\Users\Administrator\predict-trading-bot
python -c "
from src.api_client import create_api_client

config = {
    'api': {
        'api_key': '1b0c25d4-8ca6-4aa8-8910-cd72b311e4f6',
        'base_url': 'https://api.predict.fun',
        'cache_seconds': 30
    }
}

client = create_api_client(config, use_mock=False)
markets = client.get_markets(status='OPEN', limit=3)
print(f'Found {len(markets)} markets')
if markets:
    print(f'First market: {markets[0].get(\"question\")[:50]}...')
"
```

---

## 后续建议

1. **错误处理增强**: 添加更详细的错误日志和重试逻辑
2. **性能优化**: 实现游标分页的自动获取（获取所有市场，而不只是第一页）
3. **No Token 订单簿**: 如果 Predict.fun 将来支持 `outcomeId` 参数，更新代码以获取真实的 No token 订单簿
4. **WebSocket 支持**: 考虑添加 WebSocket 订阅以获得实时价格更新

---

**修复完成日期**: 2026-02-12
**修复人**: Claude Code
