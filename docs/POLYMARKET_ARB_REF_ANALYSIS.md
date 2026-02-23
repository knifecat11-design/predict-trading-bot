# polymarket-arb-ref 项目分析报告

## 📋 项目概述

`polymarket-arb-ref` 是一个**单平台套利监控工具**，专注于在 **Polymarket 平台内部**检测套利机会，基于 IMDEA Networks 研究（2024-2025 年提取了 $39.59M 套利）。

---

## 🎯 核心功能

### 1. **Single-Condition Arbitrage（单条件套利）**
- **策略**：检测同一市场内 YES + NO ≠ $1.00 的情况
- **原理**：当 YES 价格 + NO 价格 < $1.00 时，同时买入 YES 和 NO 可锁定利润
- **研究数据**：$10.58M 提取，7,051 个条件
- **示例**：
  ```
  YES 价格: $0.53
  NO 价格:  $0.42
  合计:     $0.95 < $1.00
  套利空间: $0.05 (5.3% ROI)
  ```

### 2. **NegRisk Rebalancing（负风险再平衡）**
- **策略**：检测多结果市场（≥3 个选项）中所有价格总和 ≠ $1.00
- **原理**：当所有结果价格总和 < $1.00 时，买入所有结果可锁定利润
- **研究数据**：$28.99M 提取，662 个市场，**29× 资本效率优势**
- **示例**：
  ```
  民主党: 47%
  共和党: 46%
  平局:   3%
  其他:   2%
  合计:   98% < 100%
  套利空间: 2% ROI
  ```

### 3. **Whale Tracking（鲸鱼跟踪）**
- **策略**：跟踪大额交易（>$5K），分析买卖流向
- **原理**：研究显示鲸鱼信号在 T+15 到 T+60 分钟内预测价格走势的准确率为 61-68%
- **研究数据**：顶级交易者通过此策略赚取 $2.01M（4,049 笔交易）

---

## 🔄 与现有项目 (predict-trading-bot) 的对比

| 维度 | **predict-trading-bot** | **polymarket-arb-ref** |
|------|------------------------|------------------------|
| **套利类型** | 跨平台套利 | 单平台套利 |
| **平台范围** | Polymarket ↔ Predict.fun | 仅 Polymarket |
| **策略** | Yes+No < 100%（跨平台） | 1. YES+NO≠$1.00（单市场）<br>2. Σprices≠$1.00（多结果）<br>3. 鲸鱼跟踪 |
| **市场匹配** | 需要匹配两个平台上的相同市场 | 不需要匹配，单平台内检测 |
| **API 需求** | 需要 Predict.fun API Key | 仅需 Polymarket 公开 API（无需认证） |
| **复杂度** | 中等（跨平台匹配） | 低（单平台） |

---

## ⚠️ 冲突分析

### ✅ **无直接冲突**

1. **API 使用**：
   - 两者都使用 Polymarket API，但**用途不同**
   - `predict-trading-bot` 用于获取市场数据做跨平台对比
   - `polymarket-arb-ref` 用于检测单平台内价格偏差
   - **可以同时运行**，不会互相干扰

2. **功能互补**：
   - `predict-trading-bot`：发现**跨平台价差**机会
   - `polymarket-arb-ref`：发现**单平台内价格偏差**机会
   - **两者可以并行使用**，扩大套利机会覆盖范围

3. **依赖包**：
   - `polymarket-arb-ref` 使用：`aiohttp`, `websockets`, `pandas`, `numpy`
   - `predict-trading-bot` 使用：`requests`, `yaml`, `python-telegram-bot` 等
   - **无冲突**，可以共存

---

## 💡 整合建议

### 方案 1：独立运行（推荐）

**优点**：
- 互不干扰，各自专注自己的策略
- 可以同时监控两种类型的套利机会
- 维护简单

**实施**：
```bash
# 终端1：运行跨平台套利监控
cd predict-trading-bot
python arbitrage_main.py

# 终端2：运行单平台套利监控
cd polymarket-arb-ref
python prediction_market_arbitrage.py
```

### 方案 2：整合到现有项目

**优点**：
- 统一管理，单一入口
- 可以共享 Telegram 通知系统
- 统一日志和配置

**实施步骤**：
1. 将 `polymarket-arb-ref` 的策略模块复制到 `predict-trading-bot/src/`
2. 在 `arbitrage_main.py` 中添加单平台套利检测
3. 统一通知格式，区分「跨平台套利」和「单平台套利」

---

## 📊 策略价值对比

### 跨平台套利（predict-trading-bot）
- **优势**：价差可能更大（不同平台效率差异）
- **劣势**：需要匹配市场、需要两个 API、执行复杂度高

### 单平台套利（polymarket-arb-ref）
- **优势**：
  - ✅ 无需市场匹配
  - ✅ 仅需 Polymarket API（公开，无需认证）
  - ✅ NegRisk 策略有 29× 资本效率优势
  - ✅ 执行简单（同一平台内操作）
- **劣势**：机会可能较少（市场效率较高）

---

## 🎯 推荐使用场景

### 使用 predict-trading-bot（跨平台套利）
- ✅ 已获得 Predict.fun API Key
- ✅ 愿意在两个平台间转移资金
- ✅ 需要更大的价差机会

### 使用 polymarket-arb-ref（单平台套利）
- ✅ 只想在 Polymarket 操作
- ✅ 没有 Predict.fun API Key
- ✅ 希望利用 NegRisk 的 29× 效率优势
- ✅ 想跟踪鲸鱼交易信号

### 同时使用两者（最佳）
- ✅ **最大化套利机会覆盖**
- ✅ 跨平台 + 单平台双重检测
- ✅ 互补策略，提高收益潜力

---

## 🔧 技术细节

### polymarket-arb-ref 的 API 使用

```python
# 使用 Polymarket CLOB API（公开，无需认证）
BASE_URL = "https://clob.polymarket.com"

# 获取市场列表
GET /markets?limit=100&active=true

# 获取订单簿
GET /book?token_id={token_id}

# 获取交易记录（用于鲸鱼跟踪）
GET /trades?condition_id={condition_id}&limit=100
```

### 与 predict-trading-bot 的 API 对比

| API | predict-trading-bot | polymarket-arb-ref |
|-----|-------------------|-------------------|
| **Polymarket** | ✅ 使用 Gamma API | ✅ 使用 CLOB API |
| **Predict.fun** | ✅ 需要 API Key | ❌ 不使用 |
| **认证** | Predict.fun 需认证 | 全部公开 API |

---

## 📝 代码质量对比

### polymarket-arb-ref
- ✅ 基于学术研究（IMDEA Networks）
- ✅ 详细的策略实现和风险评分
- ✅ 完整的诊断和日志系统
- ✅ 支持批量市场扫描（最多 200 个市场）

### predict-trading-bot
- ✅ 跨平台匹配逻辑
- ✅ Telegram 通知集成
- ✅ Railway 部署支持
- ✅ WebSocket 实时监控

---

## 🚀 行动建议

### 立即可做

1. **并行运行两个项目**：
   ```bash
   # 终端1
   cd predict-trading-bot && python arbitrage_main.py
   
   # 终端2  
   cd polymarket-arb-ref && python prediction_market_arbitrage.py
   ```

2. **对比机会频率**：
   - 观察一周内两种策略发现的机会数量
   - 记录实际可执行的套利机会
   - 评估各自的 ROI

3. **选择性整合**：
   - 如果 `polymarket-arb-ref` 的 NegRisk 策略效果好，可以考虑整合到主项目
   - 统一 Telegram 通知格式，方便管理

### 长期优化

1. **整合策略模块**：
   - 将 `polymarket-arb-ref` 的 `ArbitrageDetector` 类整合到 `predict-trading-bot/src/`
   - 添加配置开关，可选择启用哪些策略

2. **统一通知系统**：
   - 在 Telegram 消息中标注套利类型（跨平台 / 单平台 / NegRisk / 鲸鱼）
   - 统一风险评分和 ROI 显示格式

3. **共享市场数据**：
   - 两个项目都获取 Polymarket 数据，可以共享缓存
   - 减少 API 调用，提高效率

---

## ⚠️ 注意事项

1. **API 速率限制**：
   - 两个项目同时运行会增加对 Polymarket API 的调用
   - 注意控制扫描频率，避免触发速率限制

2. **资源消耗**：
   - `polymarket-arb-ref` 扫描更多市场（最多 200 个）
   - 确保服务器/本地机器有足够资源

3. **通知去重**：
   - 如果整合，需要确保同一机会不会重复通知
   - 建议使用市场 ID + 策略类型作为去重键

---

## 📚 参考资料

- **polymarket-arb-ref README**: 详细的策略说明和研究数据
- **IMDEA Networks 研究**: "Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets" (2025)
- **Polymarket API 文档**: https://docs.polymarket.com/

---

## ✅ 结论

**`polymarket-arb-ref` 与 `predict-trading-bot` 无冲突，可以互补使用。**

- ✅ **无 API 冲突**：两者使用不同的 API 端点或用途
- ✅ **策略互补**：跨平台套利 + 单平台套利
- ✅ **可以并行运行**：同时监控更多机会
- ✅ **建议整合**：长期可以将单平台策略整合到主项目，统一管理

**推荐做法**：先并行运行观察效果，再决定是否整合。

---

**分析日期**: 2026-02-19  
**分析人**: AI Assistant
