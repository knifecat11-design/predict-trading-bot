# 🎲 跨平台预测市场套利监控系统

![Railway](https://img.shields.io/badge/deployment-Railway-0e0c2e.svg)](https://railway.app/)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> 实时监控 Polymarket、Opinion.trade、Predict.fun 三个预测市场平台的价格差异，通过 Telegram 和 Web Dashboard 即时推送套利机会。

---

## ✨ 核心功能

### 🔍 三平台套利监控
- **实时价格追踪**: 每 30 秒扫描所有平台市场
- **智能市场匹配**: 使用统一匹配器（两层策略 + 硬约束）
- **双向套利检测**: 自动识别跨平台套利机会（A买Yes + B买No / A买No + B买Yes）
- **Telegram 推送**: 即时推送，避免重复提醒

### 📊 支持的数据源

| 平台 | API 状态 | 市场数量 | 说明 |
|---------|----------|----------|------|
| **Polymarket** | ✅ 公开 API | ~3000 | 无需密钥，Gamma API |
| **Opinion.trade** | ✅ 需配置 | ~500 | BNB Chain，需 API Key |
| **Predict.fun** | ⚠️ 待激活 | ~50 | 需申请活跃市场权限 |

### 🎯 套利策略

**核心原理**: 在预测市场中，同一市场的 `Yes价格 + No价格 = 100%`

当跨平台时，如果 `Yes + No < 100%`，则存在套利机会。

**示例**:
```
Polymarket Yes价格: 40¢
Opinion.trade No价格: 50¢
组合价格: 90¢ < 100¢
套利空间: 10%
```

---

## 🏗️ 技术架构

### 统一市场匹配模块 (`src/market_matcher.py`)

参考 dr-manhattan 架构，实现两层匹配策略：

#### 层级 1: 手动映射（100% 准确）
```python
MANUAL_MAPPINGS = [
    ManualMapping(
        slug="trump-president-2028",
        description="Will Trump be president in 2028?",
        outcomes={
            "yes": {
                "polymarket": OutcomeRef("polymarket", "condition-id-xxx", "Yes"),
                "opinion": OutcomeRef("opinion", "42", "Yes"),
                "predict": OutcomeRef("predict", "market-id-yyy", "Yes"),
            }
        }
    ),
]
```

#### 层级 2: 自动匹配（加权多因子评分）
- **硬约束**: 年份/价格必须匹配（防止 "Trump 2024" vs "Trump 2028"）
- **加权评分**: 实体 0.4 + 数字 0.3 + 词汇 0.2 + 字符串 0.1
- **一对一匹配**: 防止重复匹配

### 关键词提取器
```python
class KeywordExtractor:
    PATTERNS = {
        'year': r'\b(20[12][0-9]|20[3-9][0-9])\b',
        'price': r'\$[\d,]+(?:\.\d+)?|\d+\s*(?:dollars?|USD|million|billion)',
        'trump': r'\bTrump\b',
        'crypto': r'\b(?:Bitcoin|BTC|Ethereum|ETH|crypto)\b',
    }
```

---

## 🚀 快速开始

### 方式 1: Railway 一键部署（推荐）

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=https://github.com/knifecat11-design/predict-trading-bot)

1. 点击上方 **"Deploy on Railway"** 按钮
2. 配置环境变量：
   ```bash
   OPINION_API_KEY=your_opinion_api_key
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id
   MIN_ARBITRAGE_THRESHOLD=2.0
   ```
3. 部署完成！自动开始监控

### 方式 2: 本地运行（开发调试）

#### 1. 安装依赖
```bash
pip install -r requirements.txt
```

#### 2. 配置环境变量

创建 `.env` 文件：
```bash
OPINION_API_KEY=your_opinion_api_key
PREDICT_API_KEY=your_predict_api_key
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
MIN_ARBITRAGE_THRESHOLD=2.0
SCAN_INTERVAL=30
```

#### 3. 运行监控程序
```bash
# 后台监控
python continuous_monitor.py

# Web Dashboard
python web/dashboard.py
```

访问: http://localhost:5000

---

## 📁 项目结构

```
predict-trading-bot/
├── continuous_monitor.py      # 主监控循环（三平台套利 + TG推送）
├── start_arbitrage.py        # Railway 启动入口
├── config.yaml               # 配置文件
├── requirements.txt          # Python 依赖
├── railway.json             # Railway 部署配置
│
├── web/
│   ├── dashboard.py         # Flask Web 仪表板
│   └── templates/
│       └── index.html      # 暗色主题前端
│
├── scripts/
│   └── opinion_order.py     # Opinion CLOB 挂单工具
│
└── src/
    ├── market_matcher.py    # 统一市场匹配模块 ✨
    ├── polymarket_api.py   # Polymarket API 客户端（分页）
    ├── opinion_api.py      # Opinion API 客户端（分页）
    ├── api_client.py       # Predict.fun API 客户端
    ├── config_helper.py    # 配置加载（环境变量覆盖）
    └── notifier.py        # Telegram 通知
```

---

## 🔧 配置说明

### 必需环境变量

| 变量 | 说明 | 默认值 |
|---------|------|---------|
| `OPINION_API_KEY` | Opinion API 密钥 | - |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | - |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID | - |
| `MIN_ARBITRAGE_THRESHOLD` | 最小套利阈值（%） | 2.0 |
| `SCAN_INTERVAL` | 扫描间隔（秒） | 30 |

### 配置文件示例

```yaml
# API 配置
opinion:
  api_key: "your_opinion_api_key"
  base_url: "https://proxy.opinion.trade:8443/openapi"

api:
  api_key: "your_predict_api_key"
  base_url: "https://api.predict.fun"

# 套利配置
arbitrage:
  min_threshold: 2.0      # 最小套利阈值（%）
  scan_interval: 30         # 扫描间隔（秒）
  cooldown_minutes: 10       # 通知冷却时间（分钟）

# 通知配置
notification:
  telegram:
    enabled: true
    chat_id: "your_telegram_chat_id"
```

---

## 📝 部署日志

### 最近更新

- **2026-02-13**: 统一市场匹配模块重构（参考 dr-manhattan 架构）
- **2026-02-13**: 修复 HTML 标签干扰匹配问题（使用纯文本）
- **2026-02-13**: 修复 datetime 作用域错误
- **2026-02-13**: 修复套利检测逻辑（未定义变量引用）
- **2026-02-13**: 恢复 continuous_monitor.py（文件位置修复）
- **2026-02-11**: 添加市场去重和结束日期验证
- **2026-02-10**: 添加 Web Dashboard 和全站分页扫描
- **2026-02-09**: Opinion API 激活与配置

### Git 提交

```
5b60852 - Fix market matching: use plain text titles instead of HTML
ba15d71 - Fix datetime scope error in find_cross_platform_arbitrage
ea2fdd3 - Fix arbitrage detection: correct find_cross_platform_arbitrage function
ad36af0 - Fix crash: restore continuous_monitor.py and fix bugs
```

---

## ⚠️ 注意事项

1. **API 密钥安全**
   - 不要将 API 密钥提交到公开仓库
   - 使用环境变量或本地配置文件

2. **部署前检查**
   - 确认 `railway.json` 配置正确
   - 验证环境变量已设置

3. **本地测试**
   - 部署前先本地测试功能
   - 使用 `python continuous_monitor.py` 测试监控

---

## 📄 许可证

MIT License

Copyright (c) 2025 Predict Trading Bot

---

## 🤝 贡献者

- Core development: [Your Name]

---

**有问题？** 查看 [项目 Issues](https://github.com/knifecat11-design/predict-trading-bot/issues) 或提交新 Issue

**Web Dashboard 预览**: 暗色主题，实时显示三平台状态、套利机会、市场列表
