# Predict.fun 自动化交易与套利监控系统

一个用于 predict.fun 预测市场的自动化交易、风险管理和跨平台套利监控工具。

## 功能特性

### 1. 自动化挂单交易 (main.py)
- 实时市场数据监控
- ±6% 范围内智能挂单策略
- 自动风险管理（接近成交时撤单重挂）
- 可配置的风险敞口限制

### 2. 跨平台套利监控 (arbitrage_main.py)
- 监控 Polymarket 和 Predict.fun 的 Yes+No 价格差
- **套利策略**：Yes价格 + No价格 < 100% 时存在套利空间
- 双向套利机会检测：
  - Polymarket 买Yes + Predict 买No
  - Predict 买Yes + Polymarket 买No
- Telegram 推送套利通知
- 通知冷却时间避免重复提醒

### 项目结构
```
predict-trading-bot/
├── config.yaml              # 配置文件
├── main.py                  # 自动化挂单主程序
├── arbitrage_main.py        # 套利监控主程序
├── requirements.txt         # Python依赖包
├── src/
│   ├── __init__.py
│   ├── api_client.py        # Predict.fun API客户端
│   ├── polymarket_api.py    # Polymarket API客户端
│   ├── order_manager.py     # 订单管理器
│   ├── strategy.py          # 挂单策略
│   ├── risk_manager.py      # 风险管理
│   ├── arbitrage_monitor.py # 套利监控
│   └── notifier.py          # Telegram通知
└── logs/                    # 日志目录
```

---

## 安装步骤

### 1. 安装 Python
- 访问 https://www.python.org/downloads/
- 下载并安装 Python 3.10 或更高版本
- **安装时务必勾选 "Add Python to PATH"**

### 2. 验证安装
打开 CMD 或 PowerShell，运行：
```cmd
python --version
pip --version
```

### 3. 安装依赖
```cmd
cd C:\Users\Administrator\predict-trading-bot
pip install -r requirements.txt
```

---

## 使用方法

### 方式一：自动化挂单交易
适用于在 Predict.fun 上自动挂单获取奖励积分

1. 编辑 `config.yaml` 配置参数
2. 运行：
   ```cmd
   python main.py
   ```
3. 按 `Ctrl+C` 停止程序

### 方式二：套利监控
适用于监控两个平台的套利机会

#### 步骤1：配置 Telegram

**获取 Bot Token:**
1. 在 Telegram 中搜索 `@BotFather`
2. 发送 `/newbot` 创建新机器人
3. 按提示设置机器人名称
4. 复制获得的 Token（格式：`123456:ABC-DEF1234...`）

**获取 Chat ID:**
1. 在 Telegram 中搜索 `@userinfobot`
2. 发送任意消息
3. 复制获得的 `Id`（数字，如：`123456789`）

#### 步骤2：编辑配置
在 `config.yaml` 中填入：
```yaml
notification:
  telegram:
    enabled: true
    bot_token: "你的Bot Token"
    chat_id: "你的Chat ID"
```

#### 步骤3：运行套利监控
```cmd
python arbitrage_main.py
```

### 方式三：组合使用（推荐）
1. 先运行套利监控发现机会
2. 手动执行套利交易
3. 在相应平台运行 `main.py` 进行挂单管理

---

## 套利策略说明

### 核心原理
在预测市场中，**Yes价格 + No价格 应该 = 100%**

当 **Yes + No < 100%** 时，同时买入Yes和No可以锁定利润。

### 示例
```
Polymarket Yes价格: 40%
Predict No价格:   50%
合计: 40% + 50% = 90% < 100%
套利空间: 10%

操作：
- Polymarket: 买入 Yes (40%)
- Predict:     买入 No   (50%)
- 总成本: 90%
- 确定收益: 100%
- 利润: 10%
```

---

## 通知消息格式

当发现套利机会时，你会收到如下格式的 Telegram 通知：

```
━━━━━━━━━━━━━━━━━━━━━
📊 市场名称: 测试市场：某事件将在2026年发生

📈 利差: 10.00%
💵 组合价格: 90.0%

🔄 套利方向: Polymarket 买Yes + Predict 买No

━━━━━━━━━━━━━━━━━━━━━

📍 Polymarket
  操作: 买Yes
  Yes价格: 40.0%
  No价格: 60.0%

📍 Predict.fun
  操作: 买No
  Yes价格: 50.0%
  No价格: 50.0%

━━━━━━━━━━━━━━━━━━━━━

⏰ 时间: 2026-02-04 14:30:25
⚡ 请尽快手动执行套利！
```

---

## 配置说明

### 套利监控配置 (config.yaml)
```yaml
arbitrage:
  enabled: true                 # 启用套利监控
  min_arbitrage_threshold: 2.0  # 最小套利空间2%（Yes+No < 98%时通知）
  scan_interval: 10             # 每10秒扫描一次
  cooldown_minutes: 5           # 同一市场5分钟内只通知一次
```

### Telegram 配置
```yaml
notification:
  telegram:
    enabled: true               # 启用Telegram通知
    bot_token: "123456:ABC..."  # 从@BotFather获取
    chat_id: "123456789"        # 从@userinfobot获取
```

### 风险管理配置
```yaml
market:
  max_exposure: 100             # 最大风险敞口$100

risk:
  daily_loss_limit: 50          # 每日最大损失$50
```

---

## 注意事项

⚠️ **当前使用模拟数据进行测试**

等待 API 批准后：
1. 在 `src/api_client.py` 中实现真实的 Predict.fun API
2. 在 `src/polymarket_api.py` 中实现真实的 Polymarket API
3. 在 `config.yaml` 中填入真实的 API 密钥

⚠️ **套利风险提示**
- 实际套利需考虑滑点、流动性、资金转移时间
- 建议从小额开始测试
- 手动执行前请再次确认价格
- 确保两个平台都有足够流动性

---

## 常见问题

**Q: 为什么收不到 Telegram 通知？**
A:
1. 检查 config.yaml 中的 `notification.telegram.enabled` 是否为 `true`
2. 确认 bot_token 和 chat_id 正确
3. 尝试向 Bot 发送消息，确保 Bot 已启动
4. 检查是否在配置的阈值范围内（默认需要2%以上套利空间）

**Q: 如何测试通知功能？**
A: 运行程序时会自动发送测试消息，或在 Python 中运行：
```python
from src.notifier import TelegramNotifier
import yaml
config = yaml.safe_load(open('config.yaml'))
notifier = TelegramNotifier(config)
notifier.send_test_message()
```

**Q: 套利机会多久会出现一次？**
A: 取决于市场波动和两个平台的效率差异。配置的 `min_arbitrage_threshold` 越低，通知越频繁。

**Q: 可以同时监控多个市场吗？**
A: 可以，在 `src/arbitrage_monitor.py` 的 `_load_market_map()` 方法中添加更多市场映射。

---

## 后续扩展

- [ ] Polymarket 真实 API 接入
- [ ] Predict.fun 真实 API 接入
- [ ] 支持监控多个市场
- [ ] 历史套利机会记录和统计
- [ ] Web 界面
- [ ] 自动执行套利（可选）
