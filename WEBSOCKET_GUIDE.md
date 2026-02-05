# WebSocket 实时监控使用指南

## 概述

基于 runesatsdev/polymarket-arbitrage-bot 的成功经验，新增了 WebSocket 实时监控功能，支持：
- **6 个并行 WebSocket 连接**，每个监控 250 个市场（共 1500 个市场）
- **实时价格更新**，无需 HTTP 轮询
- **智能市场过滤**（流动性 $10k+，结算 7 天内）
- **自动套利检测**和实时通知

## 文件结构

```
predict-trading-bot/
├── src/
│   ├── polymarket_websocket.py    # WebSocket 客户端核心实现
│   ├── arbitrage_monitor.py        # 套利监控器（已集成 WebSocket）
│   ├── polymarket_api.py           # Polymarket HTTP API 客户端
│   ├── api_client.py               # Predict.fun API 客户端
│   ├── probable_api.py             # Probable.markets API 客户端
│   └── market_matcher.py           # 智能市场匹配器
├── test_websocket_monitor.py       # WebSocket 基础测试
├── test_realtime_monitoring.py     # 实时监控集成测试
└── config.yaml.example             # 配置文件示例
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

新增依赖：
- `websockets>=16.0` - WebSocket 客户端库

### 2. 配置 WebSocket

在 `config.yaml` 中添加 WebSocket 配置：

```yaml
# WebSocket 实时监控配置
websocket:
  enabled: true                 # 是否启用 WebSocket 实时监控
  num_connections: 6            # 并行 WebSocket 连接数
  markets_per_connection: 250   # 每个连接监控的市场数量
  min_liquidity: 10000          # 最小流动性要求（美元）
  max_days: 7                   # 最大结算天数
  monitor_duration: 3600        # 每次监控持续时间（秒）
```

### 3. 基础测试

```bash
python test_websocket_monitor.py
```

这将测试 WebSocket 连接、市场订阅和价格更新接收。

### 4. 集成测试

```bash
python test_realtime_monitoring.py
```

这将测试完整的实时监控和套利检测流程。

## API 使用示例

### 创建 WebSocket 客户端

```python
from src.polymarket_websocket import create_websocket_client

ws_client = create_websocket_client(
    num_connections=6,      # 6 个并行连接
    markets_per_connection=250,  # 每个连接 250 个市场
    min_liquidity=10000,    # 最小 $10k 流动性
    max_days=7              # 最多 7 天结算
)
```

### 注册价格更新回调

```python
async def on_price_update(price):
    print(f"Token: {price.token_id}, Price: ${price.price}, Side: {price.side}")

async def on_market_update(market_id, prices):
    yes_price = prices.get('yes', 0)
    no_price = prices.get('no', 0)
    spread = yes_price + no_price
    if spread < 0.98:
        print(f"Arbitrage! Market: {market_id}, Spread: {spread}")

ws_client.on_price_update(on_price_update)
ws_client.on_market_update(on_market_update)
```

### 启动监控

```python
import asyncio

# 获取市场列表
from src.polymarket_api import create_polymarket_client
poly_client = create_polymarket_client({'polymarket': {}}, use_real=True)
markets = poly_client.get_all_markets(limit=1000, active_only=True)

# 启动 WebSocket 监控
async def monitor():
    await ws_client.connect(markets)

asyncio.run(monitor())
```

### 与套利监控器集成

```python
from src.arbitrage_monitor import ArbitrageMonitor

monitor_config = {
    'arbitrage': {
        'min_arbitrage_threshold': 2.0,
        'scan_interval': 10
    },
    'websocket': {
        'enabled': True,
        'num_connections': 6,
        'markets_per_connection': 250
    }
}

monitor = ArbitrageMonitor(monitor_config)

# 注册套利机会回调
def on_arbitrage(opportunity):
    print(f"Arbitrage found: {opportunity.market_name}")
    print(f"Profit: {opportunity.arbitrage_percent}%")

monitor.on_arbitrage(on_arbitrage)

# 启动实时监控
opportunities = await monitor.start_realtime_monitoring(
    poly_client,
    predict_client,
    probable_client,
    duration_seconds=3600
)
```

## 关键特性

### 1. 并行连接

使用 6 个并行 WebSocket 连接，避免单连接的速率限制：

```python
config = WebSocketConfig(
    num_connections=6,
    markets_per_connection=250
)
```

### 2. 智能市场过滤

自动过滤市场，只监控高质量机会：

```python
filter = MarketFilter(
    min_liquidity_usd=10000,      # 至少 $10k 流动性
    max_days_until_resolution=7,   # 最多 7 天结算
    min_price=0.01,                # 价格范围
    max_price=0.99
)
```

### 3. 实时套利检测

在回调中实时检测套利机会：

```python
async def on_market_update(market_id, prices):
    yes_price = prices.get('yes', 0)
    no_price = prices.get('no', 0)

    if yes_price > 0 and no_price > 0:
        spread = yes_price + no_price
        arbitrage = (1.0 - spread) * 100

        if arbitrage >= 2.0:  # 2% 阈值
            notify_arbitrage(market_id, arbitrage)
```

### 4. 自动重连

内置自动重连机制：

```python
config = WebSocketConfig(
    reconnect_delay=5,           # 5 秒后重连
    max_reconnect_attempts=10,   # 最多重连 10 次
    ping_interval=30             # 30 秒心跳
)
```

## WebSocket 协议

### 订阅格式

```json
{
  "type": "subscribe",
  "channel": "price_level::{token_id}_YES"
}
```

### 价格更新格式

```json
{
  "type": "price_level",
  "channel": "price_level::{token_id}_YES",
  "payload": {
    "price": 0.65
  }
}
```

## 监控统计

获取实时监控统计信息：

```python
stats = ws_client.get_statistics()
print(f"Markets monitored: {stats['markets_monitored']}")
print(f"Messages received: {stats['messages_received']}")
print(f"Price updates: {stats['price_updates']}")
print(f"Active connections: {stats['connections_active']}")
```

## 注意事项

1. **API 限制**: WebSocket 连接数和订阅数可能受限制
2. **网络稳定性**: 长时间运行需要稳定的网络连接
3. **资源使用**: 6 个并发连接会占用一定内存和 CPU
4. **市场过滤**: 合理设置过滤器以减少不必要的更新

## Railway 部署

在 Railway 上部署时，确保：

1. 设置 `USE_REAL_API=true`
2. 配置 `PREDICT_API_KEY` 环境变量
3. WebSocket 连接在 Railway 的容器环境中运行正常

## 下一步

1. **测试 WebSocket**: 运行 `test_websocket_monitor.py`
2. **集成测试**: 运行 `test_realtime_monitoring.py`
3. **部署到 Railway**: 推送代码并监控运行状态
4. **性能优化**: 根据实际情况调整连接数和市场过滤参数

## 参考资料

- runesatsdev/polymarket-arbitrage-bot: https://github.com/runesatsdev/polymarket-arbitrage-bot
- Polymarket CLOB API: https://docs.polymarket.com
- websockets library: https://websockets.readthedocs.io/
