# 🎲 Cross-Platform Prediction Market Arbitrage Monitor

[![Railway](https://img.shields.io/badge/deployment-Railway-0e0c2e.svg)](https://railway.app/)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-active-success.svg)]()

> **全平台预测市场套利监控系统** — 实时扫描 4 大平台价差，Telegram 即时推送 + Web 实时仪表盘。

**支持平台:** Polymarket · Opinion.trade · Predict.fun · Kalshi

---

## ✨ 核心特性

### 🔍 全平台双向套利监控

| 特性 | 说明 |
|:---|:---|
| **4 平台 6 对组合** | 任意两个平台之间自动检测套利，覆盖所有 C(4,2) = 6 种配对 |
| **双向检测** | 每对平台同时检查两个方向（A买Yes+B买No，B买Yes+A买No） |
| **同平台套利** | 检测单个平台内 Yes+No < $1.00 的机会 |
| **多结果套利** | Polymarket 多结果事件（如世界杯冠军 20 个队伍，买齐所有结果 < $1） |
| **跨平台组合套利** | 同一事件在不同平台为每个结果选最低价组合（England@Kalshi + Brazil@Predict） |

### 📡 实时价格推送

- **WebSocket 实时价格**: Polymarket + Kalshi 订阅价格变动，秒级响应
- **并发订单簿抓取**: ThreadPoolExecutor 10 线程并行获取 Opinion/Predict 深度
- **轮询兜底**: WebSocket 断线自动回退轮询模式

### 🧠 智能市场匹配

- **倒排索引匹配**: O(n+m) 高效跨平台同名市场识别（**24 倍加速**，41s → 1.68s）
- **加权评分**: 实体 (40%) + 数字/日期 (30%) + 词汇 (20%) + 字符串相似度 (10%)
- **硬约束**: 年份、价格值必须一致（防止 "Trump 2024" 匹配 "Trump 2028"）
- **语义反转检测**: "Trump out" vs "Trump remain" 自动识别为对立问题

### 📊 两种部署服务

| 服务 | 功能 | 入口 |
|:---|:---|:---|
| **Monitor Bot** | Telegram 套利播报 | `start_arbitrage.py` |
| **Web Dashboard** | Flask 实时仪表盘 | `web/dashboard.py` (port 5000) |

---

## 🎯 套利策略

### 二元市场跨平台套利

**核心原理**: 在预测市场中，买 Yes + 买 No 保证获得 $1.00 回报。当跨平台购买总成本 < $1.00 时，锁定无风险利润。

```
示例:
  Polymarket  Yes Ask: 42c
  Kalshi      No  Ask: 50c
  ──────────────────────
  总成本:     92c < $1.00
  套利空间:   8%

  操作: Polymarket 买 Yes + Kalshi 买 No
  无论结果如何，回报 $1.00，净赚 8c
```

### 多结果事件套利

```
示例 (2026 世界杯冠军):
  Brazil:    12c  [Kalshi]     ← 选最低价平台
  England:    8c  [Predict]
  France:    10c  [Polymarket]
  Germany:    7c  [Kalshi]
  ... 其他队伍合计 55c
  ──────────────────────
  总成本:    92c < $1.00
  套利空间:   8%

  操作: 在各平台分别买入最低价的结果，覆盖所有结果
  冠军必定产生，保证回收 $1.00
```

---

## 📊 支持平台

| 平台 | API | 认证 | 市场数 | 价格来源 |
|:---|:---|:---|:---|:---|
| **Polymarket** | Gamma API | 无需密钥 | ~28,000 | bestAsk/bestBid |
| **Kalshi** | Public API v2 | 无需密钥 | ~4,000 | yes_ask/no_ask (内嵌) |
| **Opinion.trade** | OpenAPI | API Key | ~150 | Yes+No 独立订单簿 |
| **Predict.fun** | v1 API | API Key | ~120 | 内嵌/独立订单簿 |

---

## 🚀 快速开始

### 方式 1: Railway 一键部署（推荐）

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/knifecat11-design/predict-trading-bot)

1. 点击上方按钮
2. 在 Railway 设置环境变量（至少需要 Telegram）:
   ```bash
   TELEGRAM_BOT_TOKEN=你的Bot_Token
   TELEGRAM_CHAT_ID=你的Chat_ID
   ```
3. 部署完成！Polymarket 和 Kalshi 开箱即用（公开 API）

> 如需 Opinion/Predict 平台，额外设置 `OPINION_API_KEY` 和 `PREDICT_API_KEY`。

### 方式 2: 本地运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 Telegram Token 和 API Key

# 3. 运行 Monitor Bot（Telegram 通知）
python start_arbitrage.py

# 4. 或运行 Web Dashboard（浏览器查看）
python web/dashboard.py
# 访问 http://localhost:5000
```

---

## ⚙️ 配置说明

配置优先级: **环境变量** > `config.yaml` > 代码默认值

### 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|:---|:---:|:---|:---|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | Telegram Bot Token (from @BotFather) |
| `TELEGRAM_CHAT_ID` | ✅ | — | 目标 Chat ID |
| `PREDICT_API_KEY` | ❌ | — | Predict.fun API 密钥 |
| `OPINION_API_KEY` | ❌ | — | Opinion.trade API 密钥 |
| `MIN_ARBITRAGE_THRESHOLD` | ❌ | `2.0` | 最小套利阈值 (%) |
| `SCAN_INTERVAL` | ❌ | `60` | 扫描间隔 (秒) |
| `COOLDOWN_MINUTES` | ❌ | `5` | 同一市场通知冷却 (分钟) |
| `LOG_LEVEL` | ❌ | `INFO` | 日志级别 |
| `PORT` | ❌ | `5000` | Dashboard 端口 |

> **安全**: `config.yaml` 和 `.env` 已 gitignore，绝不要提交含密钥的文件。

---

## 📱 Telegram 通知示例

### 二元套利通知
```
🎯 套利机会 #42
市场: Will Trump win 2028 election?
平台: Polymarket <-> Kalshi
方向: Polymarket Buy Yes + Kalshi Buy No
套利空间: 3.50%

Platform A: Yes 45.0c  No 57.0c
Platform B: Yes 43.0c  No 51.5c
置信度: 85%
时间: 14:30:25
```

### 多结果套利通知
```
🎰 跨平台组合套利 #7
事件: 2026 FIFA World Cup Winner
平台: Kalshi+Predict+Polymarket
结果数: 15
总成本: 93.2c
套利空间: 6.80%

各结果价格:
  • Brazil: 12.5c [Kalshi]
  • England: 10.2c [Predict]
  • France: 9.8c [Polymarket]
  • Germany: 8.1c [Kalshi]
  • Spain: 7.5c [Predict]
  ... +10 more

时间: 14:30:25
```

---

## 📁 项目结构

```
predict-trading-bot/
├── continuous_monitor.py      # Monitor Bot 主循环 + Telegram 通知
├── start_arbitrage.py         # Railway 入口（添加项目根到 path）
├── src/
│   ├── polymarket_api.py      # Polymarket Gamma API (公开, ~28k 市场)
│   ├── kalshi_api.py          # Kalshi Public API v2 (~4k 市场, 游标分页)
│   ├── opinion_api.py         # Opinion.trade API (SDK + HTTP 降级)
│   ├── api_client.py          # Predict.fun v1 API + MockAPIClient
│   ├── market_matcher.py      # 跨平台市场匹配 (倒排索引, 24x 加速)
│   ├── ws_price_feed.py       # WebSocket 实时价格 (Polymarket + Kalshi)
│   └── config_helper.py       # 配置加载 (env > yaml > default)
├── web/
│   ├── dashboard.py           # Flask + SocketIO 实时仪表盘
│   └── templates/
│       └── index.html         # Dashboard 前端
├── docs/                      # 文档 (中英混合)
├── config.yaml.example        # 完整配置模板
├── .env.example               # 环境变量模板
├── requirements.txt           # Python 依赖
├── railway.json               # Railway 部署配置
└── nixpacks.toml              # 构建配置 (Python 3.11)
```

---

## 🏗️ 系统架构

```
                    ┌─────────────────────────────────────────┐
                    │           Market Data Sources            │
                    ├──────────┬──────────┬─────────┬─────────┤
                    │Polymarket│  Kalshi  │ Opinion │ Predict │
                    │ (Gamma)  │(Public v2│ (HTTP)  │ (v1 API)│
                    │ +WS Feed │ +WS Feed)│         │         │
                    └────┬─────┴────┬─────┴────┬────┴────┬────┘
                         │          │          │         │
                         ▼          ▼          ▼         ▼
                    ┌─────────────────────────────────────────┐
                    │      ThreadPoolExecutor (10 workers)     │
                    │        Concurrent Price Fetching         │
                    └──────────────────┬──────────────────────┘
                                       │
                         ┌─────────────┼─────────────┐
                         ▼             ▼             ▼
                   ┌───────────┐ ┌──────────┐ ┌───────────┐
                   │  Market   │ │  Binary  │ │  Multi-   │
                   │  Matcher  │ │ Arb      │ │  Outcome  │
                   │(Inverted  │ │ Detector │ │  Arb      │
                   │  Index)   │ │(6 pairs) │ │ Detector  │
                   └─────┬─────┘ └────┬─────┘ └─────┬─────┘
                         │            │              │
                         ▼            ▼              ▼
               ┌──────────────────────────────────────────┐
               │          Arbitrage Opportunities          │
               └─────────────┬──────────────┬─────────────┘
                             │              │
                    ┌────────▼───────┐ ┌────▼────────────┐
                    │  Monitor Bot   │ │  Web Dashboard   │
                    │  (Telegram)    │ │  (Flask+SocketIO)│
                    │  Dedup+Cooldown│ │  Real-time Push  │
                    └────────────────┘ └─────────────────┘
```

---

## 🔑 获取 API 密钥

### Polymarket / Kalshi — 无需密钥
- **Polymarket**: [Gamma API Docs](https://docs.polymarket.com/developers/gamma-markets-api/overview)
- **Kalshi**: Public API，直接可用

### Opinion.trade — 需要 API Key
- 在 [Opinion.trade](https://app.opinion.trade/) 注册后获取

### Predict.fun — 需要申请
1. 访问 [dev.predict.fun](https://dev.predict.fun/)
2. 加入 Discord 开工单申请
3. 获得密钥后设置 `PREDICT_API_KEY`

详见 [docs/API申请指南.md](docs/API申请指南.md)

---

## 🔧 技术栈

| 技术 | 版本 | 用途 |
|:---|:---|:---|
| **Python** | 3.11+ | 核心语言 |
| **Flask** | 3.0+ | Web 框架 |
| **Flask-SocketIO** | 5.3+ | 实时通信 |
| **requests** | 2.31+ | HTTP 客户端 |
| **websocket-client** | 1.6+ | WebSocket 客户端 |
| **PyYAML** | 6.0+ | 配置解析 |
| **python-dotenv** | 1.0+ | 环境变量 |

---

## ❓ 常见问题

### Q: 为什么收不到 Telegram 通知？
**A:** 请检查：
1. `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID` 是否正确
2. 尝试向 Bot 发送消息，确保 Bot 已启动
3. 检查是否达到配置的阈值范围（默认需要 2% 以上套利空间）

### Q: 如何测试通知功能？
**A:** 运行程序时会自动发送测试消息。

### Q: 套利机会多久会出现一次？
**A:** 取决于市场波动和平台效率差异。配置的 `MIN_ARBITRAGE_THRESHOLD` 越低，通知越频繁。

### Q: 可以同时监控多个市场吗？
**A:** 可以，系统默认监控所有支持的市场。

---

## ⚠️ 风险提示

- 套利机会转瞬即逝，发现后需快速执行
- 实际交易需考虑**滑点、流动性、资金转移时间**
- 平台手续费约 2%，需从套利空间中扣除
- 建议从小额开始测试
- 本工具**仅提供监控和通知，不执行自动交易**

---

## 📚 文档

- [API 申请指南](docs/API申请指南.md)
- [Railway 部署指南](docs/RAILWAY_DEPLOY.md)
- [Railway Web 部署](docs/RAILWAY_WEB_DEPLOY.md)
- [项目结构说明](docs/PROJECT_STRUCTURE.md)

---

## 🗺️ 开发路线图

- [ ] 增加更多预测平台支持
- [ ] 历史套利机会记录和统计
- [ ] 移动端适配
- [ ] 自动执行套利（可选）

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

---

## 📄 许可证

MIT License - 详见 [LICENSE](LICENSE) 文件

---

## 🙏 致谢

- [Polymarket](https://polymarket.com/) - 预测市场平台
- [Kalshi](https://kalshi.com/) - 预测市场平台
- [Opinion.trade](https://app.opinion.trade/) - 预测市场平台
- [Predict.fun](https://predict.fun/) - 预测市场平台
- [Railway](https://railway.app/) - 部署平台

---

<div align="center">

**⚠️ 注意**: 本工具仅供学习和研究使用。使用本工具进行实际交易的风险由使用者自行承担。

Made with ❤️ by [knifecat11-design](https://github.com/knifecat11-design)

</div>
