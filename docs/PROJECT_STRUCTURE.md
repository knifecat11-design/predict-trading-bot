# 项目结构说明

## 架构概览

双服务 Railway 部署架构，监控三个预测市场平台的套利机会：
- **Polymarket** (~3000 市场)
- **Opinion.trade** (~150 市场)
- **Predict.fun** (~123 市场)

## 📁 根目录

### 核心程序
- **start_arbitrage.py** - Monitor Bot 入口（Railway 服务 1）
- **continuous_monitor.py** - 主监控逻辑（Telegram 通知 + 套利扫描）

### 配置文件
- **config.yaml** - 本地配置（敏感信息，不提交 Git）
- **requirements.txt** - Python 依赖
- **nixpacks.toml** - Railway 构建配置
- **railway.json** - Railway 部署配置（环境变量）

## 📂 web/ 目录

Railway 服务 2 - Web Dashboard
- **web/dashboard.py** - Flask + SocketIO 实时仪表板 (v3.2)
- **web/templates/index.html** - 仪表板前端模板

## 📂 src/ 目录

### API 客户端
- **api_client.py** - Predict.fun API 客户端 (v1 API)
  - `PredictAPIClient` - 真实 API（x-api-key 认证）
  - `MockAPIClient` - 模拟客户端
  - `MarketData` / `Order` - 数据类

- **polymarket_api.py** - Polymarket API 客户端
  - Gamma API 数据获取（全站分页，3000 市场）

- **opinion_api.py** - Opinion.trade API 客户端
  - SDK + HTTP 双模式价格获取（mid-market 价格）

### 核心功能
- **market_matcher.py** - 跨平台市场匹配
  - 加权多因子评分 + 硬约束（年份/价格必须匹配）
  - 一对一匹配防止重复

- **config_helper.py** - 配置辅助（环境变量优先）

## 📂 docs/ 目录

- **API申请指南.md** - API 访问申请指南
- **RAILWAY_DEPLOY.md** - Railway 部署指南
- **RAILWAY_WEB_DEPLOY.md** - Web 服务部署指南
- **快速部署.md** - 快速部署步骤
- **predict_fun_api申请模板.md** - Predict.fun API 申请模板
- **PREDICT_AUTO_ORDER.md** - 自动下单文档

## 📊 数据流

```
┌──────────────────────────────────────────────┐
│            start_arbitrage.py                  │
│            (Monitor Bot 入口)                  │
└──────────────┬───────────────────────────────┘
               │
     ┌─────────▼─────────┐
     │ continuous_monitor  │
     │  (主监控循环)        │
     └─────────┬─────────┘
               │
    ┌──────────┼──────────┐
    │          │          │
┌───▼───┐ ┌───▼───┐ ┌───▼───┐
│Polymarket│ │Opinion │ │Predict│
│(~3000)  │ │(~150)  │ │(~123) │
└───┬───┘ └───┬───┘ └───┬───┘
    │          │          │
    └──────────┼──────────┘
               │
     ┌─────────▼─────────┐
     │  Telegram 通知      │
     │  (套利机会推送)      │
     └───────────────────┘

┌──────────────────────────────────────────────┐
│           web/dashboard.py                    │
│           (Web Dashboard)                     │
└──────────────┬───────────────────────────────┘
               │
    ┌──────────┼──────────┐
    │          │          │
┌───▼───┐ ┌───▼───┐ ┌───▼───┐
│Polymarket│ │Opinion │ │Predict│
└───┬───┘ └───┬───┘ └───┬───┘
    │          │          │
    └──────────┼──────────┘
               │
     ┌─────────▼─────────┐
     │  MarketMatcher     │
     │  (跨平台匹配)       │
     └─────────┬─────────┘
               │
     ┌─────────▼─────────┐
     │  Flask + SocketIO  │
     │  (实时仪表板)       │
     └───────────────────┘
```

## 📝 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| TELEGRAM_BOT_TOKEN | ✅ | Telegram Bot Token |
| TELEGRAM_CHAT_ID | ✅ | Telegram Chat ID |
| PREDICT_API_KEY | ✅ | Predict.fun API Key |
| PREDICT_BASE_URL | - | Predict API 地址 (默认 https://api.predict.fun) |
| OPINION_API_KEY | ✅ | Opinion.trade API Key |
| MIN_ARBITRAGE_THRESHOLD | - | 最小套利阈值 % (默认 2.0) |
| SCAN_INTERVAL | - | 扫描间隔秒 (默认 60) |
| COOLDOWN_MINUTES | - | 冷却时间分钟 (默认 5) |

---

**更新日期**: 2026-02-17
