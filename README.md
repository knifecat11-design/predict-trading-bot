# 🎲 Polymarket ↔ Predict.fun 套利监控系统

![Railway](https://img.shields.io/badge/deployment-Railway-0e0c2e.svg)](https://railway.app/)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> 跨平台预测市场套利机会实时监控工具 | 实时监控 Polymarket 和 Predict.fun 的价格差异，通过 Telegram 即时推送套利机会。

## ✨ 核心功能

### 🔍 实时套利监控
- **实时价格追踪**: 每 30 秒扫描 Polymarket 和 Predict.fun 市场价格
- **双向套利检测**: 自动识别两平台之间的套利机会
- **智能通知**: Telegram 即时推送，避免重复提醒（5 分钟冷却）
- **灵活配置**: 自定义最小套利阈值（默认 2%）

### 📊 支持的数据源

| 平台 | API 状态 | 说明 |
|---------|----------|------|
| **Polymarket** | ✅ 公开 API | 无需密钥，直接访问 [Gamma API](https://docs.polymarket.com/developers/gamma-markets-api/overview) |
| **Predict.fun** | 🔑 需申请 | 通过 [Discord](https://dev.predict.fun/) 申请 API 密钥 |

### 🎯 套利策略

**核心原理**: 在预测市场中，`Yes价格 + No价格 = 100%`

当 `Yes + No < 100%` 时，同时买入 Yes 和 No 可以锁定利润。

**示例**:
```
Polymarket Yes价格: 40%
Predict.fun No价格: 50%
组合价格: 90% < 100%
套利空间: 10%
```

## 🚀 快速开始

### 方式 1: Railway 一键部署（推荐）

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/knifecat11-design/predict-trading-bot)

1. 点击上方 **"Deploy on Railway"** 按钮
2. 在 Railway 设置环境变量：
   ```bash
   TELEGRAM_BOT_TOKEN=你的Bot_Token
   TELEGRAM_CHAT_ID=你的Chat_ID
   ```
3. 部署完成！自动开始监控

### 方式 2: 本地运行（开发调试）

#### 1. 安装依赖
```bash
pip install -r requirements.txt
```

#### 2. 配置环境变量

创建 `.env` 文件（参考 `config.yaml.example`）：
```bash
OPINION_API_KEY=your_opinion_api_key
PREDICT_API_KEY=your_predict_api_key
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
```

#### 3. 运行 Dashboard
```bash
cd web
python dashboard.py
```

访问: http://localhost:5000

## 📁 项目结构

```
predict-trading-bot/
├── web/
│   └── dashboard.py          # Flask Web 服务器（主程序）
├── src/
│   ├── polymarket_api.py    # Polymarket API 客户端
│   ├── opinion_api.py        # Opinion API 客户端
│   ├── api_client.py         # Predict API 客户端
│   └── polymarket_clob_client.py  # Polymarket CLOB 客户端
├── requirements.txt           # Python 依赖
├── config.yaml.example      # 配置文件示例
├── railway.json             # Railway 部署配置
└── README.md               # 项目说明
```

## 🎯 核心模块说明

### Web Dashboard (`web/dashboard.py`)

**功能**:
- 实时套利监控
- 三平台数据展示（Polymarket、Opinion、Predict）
- 套利机会自动计算
- Telegram 通知集成

**API 端点**:
- `/api/state` - 返回系统状态（市场数据、套利机会）
- `/health` - 健康检查端点

### API 客户端 (`src/*.py`)

#### Polymarket (`polymarket_api.py`)
- 使用 Gamma API 获取市场数据
- 支持 `bestAsk/bestBid` 字段（真实订单簿价格）

#### Opinion (`opinion_api.py`)
- 使用 Opinion.trade API
- 获取订单簿 size（可买份额）

#### Predict (`api_client.py`)
- 使用 Predict.fun API
- 支持完整订单簿获取

## 🔧 配置说明

### 必需环境变量

| 变量 | 说明 | 默认值 |
|---------|------|---------|
| `OPINION_API_KEY` | Opinion API 密钥 | - |
| `PREDICT_API_KEY` | Predict API 密钥 | - |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | - |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID | - |
| `SCAN_INTERVAL` | 扫描间隔（秒） | 30 |
| `MIN_ARBITRAGE_THRESHOLD` | 最小套利阈值（%） | 2.0 |
| `COOLDOWN_MINUTES` | 通知冷却时间（分钟） | 5 |

### 配置文件示例

复制 `config.yaml.example` 并重命名为 `config.yaml`：
```yaml
# API 配置
opinion:
  api_key: "your_opinion_api_key"
  base_url: "https://proxy.opinion.trade:8443/openapi"

api:
  api_key: "your_predict_api_key"
  base_url: "https://api.predict.fun"

# 通知配置
telegram:
  bot_token: "your_telegram_bot_token"
  chat_id: "your_telegram_chat_id"

# 套利配置
arbitrage:
  min_threshold: 2.0  # 最小套利阈值（%）
  scan_interval: 30      # 扫描间隔（秒）
  cooldown_minutes: 5     # 通知冷却时间（分钟）
```

## 📝 部署日志

### 最近更新

- **2025-02-11**: 项目初始化，基础监控功能实现
- **2025-02-12**: 添加 Railway 部署支持，优化 API 调用逻辑
- **2025-02-12**: 修复 Polymarket 价格获取（使用 bestAsk/bestBid）
- **2025-02-12**: 添加 Opinion 订单簿 size 显示
- **2025-02-12**: 优化 UI 布局（平台链接、Amount 格式化）

## 📄 许可证

MIT License

Copyright (c) 2025 Predict Trading Bot

## 🤝 贡献者

- Core development: [Your Name]

## ⚠️ 注意事项

1. **API 密钥安全**
   - 不要将 API 密钥提交到公开仓库
   - 使用环境变量或本地配置文件
   - Railway 环境变量已配置示例密钥（请替换为真实密钥）

2. **部署前检查**
   - 确认 `railway.json` 配置正确
   - 验证环境变量已设置

3. **本地测试**
   - 部署前先本地测试功能
   - 使用 `python web/dashboard.py` 启动本地服务器

## 🎯 快速开始

1. [Railway 一键部署](#方式-1-railway一键部署)
2. 本地运行并调试

---

**有问题？** 查看 [项目 Issues](https://github.com/knifecat11-design/predict-trading-bot/issues) 或提交新 Issue
