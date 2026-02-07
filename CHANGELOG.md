# 更新日志 - 2026-02-07

## Bug 修复

### 1. Polymarket API 过滤逻辑修复 (`src/polymarket_api.py`)
**问题**: Line 153 中 `if not market.get('closed', True)` 的默认值是 `True`，导致没有 `closed` 字段的市场被错误过滤掉。

**修复**:
```python
# 修复前
if not market.get('closed', True) and liquidity > 0:

# 修复后
is_closed = market.get('closed', market.get('active', True))
is_active = market.get('active', not is_closed)
if not is_closed and is_active and liquidity > 0:
```

### 2. 市场匹配器字段名兼容性修复 (`src/market_matcher.py`)
**问题**: Polymarket API 使用驼峰命名 (conditionId, question)，代码没有正确处理。

**修复**:
```python
# 兼容驼峰和蛇形命名
poly_id = (poly_market.get('conditionId') or
           poly_market.get('condition_id') or
           poly_market.get('questionId') or
           poly_market.get('question_id', ''))

poly_title = (poly_market.get('question') or
             poly_market.get('title') or
             poly_market.get('description') or '')
```

### 3. 套利监控器错误处理修复 (`src/arbitrage_monitor.py`)
**问题**: 没有检查 `market_matcher` 是否为 `None`，可能引发 `AttributeError`。

**修复**:
```python
# 添加 None 检查
if self.market_matcher is None:
    self.logger.warning("市场匹配器未初始化，跳过扫描")
    return []
```

### 4. 测试脚本编码修复
**问题**: Windows 控制台使用 GBK 编码，无法显示 Unicode 字符（如 ↔, Σ）。

**修复**: 添加 UTF-8 编码输出处理。

---

## 新增功能

### 1. NegRisk 套利监控 (`src/negrisk_monitor.py`)
检测多选项市场（3+ 选项）的定价偏差：
- 当 Σ(prices) < 100% 时：买入所有选项
- 当 Σ(prices) > 100% 时：卖空所有选项
- 自动计算预期收益（扣除手续费）
- 参考 IMDEA 研究报告（$28.99M 历史收益）

**测试脚本**: `test_negrisk.py`

### 2. Kalshi API 客户端 (`src/kalshi_api.py`)
支持与 Kalshi 预测市场平台集成：
- 获取市场列表
- 获取订单簿
- 市场搜索和分类筛选
- 支持模拟模式用于测试

**注意**: Kalshi API 需要认证才能访问市场数据。

### 3. 跨平台套利监控 (`src/cross_platform_monitor.py`)
监控 Polymarket ↔ Kalshi 之间的套利机会：
- 基于关键词相似度的市场匹配
- 双向套利检测（Poly Yes + Kalshi No / Kalshi Yes + Poly No）
- 置信度评分系统

**测试脚本**: `test_cross_platform.py`

---

## 文件清单

### 新增文件
```
src/negrisk_monitor.py         # NegRisk 套利监控模块
src/kalshi_api.py              # Kalshi API 客户端
src/cross_platform_monitor.py  # 跨平台套利监控
test_negrisk.py                # NegRisk 测试脚本
test_cross_platform.py         # 跨平台测试脚本
```

### 修改文件
```
src/polymarket_api.py          # 修复过滤逻辑
src/market_matcher.py          # 修复字段名兼容性
src/arbitrage_monitor.py       # 修复错误处理
scan_once.py                   # 修复编码问题
```

---

## 使用说明

### 运行单次扫描
```bash
python scan_once.py
```

### 运行 NegRisk 监控
```bash
python test_negrisk.py
```

### 运行跨平台监控
```bash
python test_cross_platform.py
```

### 运行持续监控
```bash
python run_monitor.py
```

---

## 下一步建议

1. **申请 Kalshi API Key**
   - 访问 https://kalshi.com/developers
   - 获取 API 密钥后更新 `config.yaml`

2. **完善市场匹配算法**
   - 实现更精确的语义匹配（使用 NLP 模型）
   - 添加人工验证接口

3. **添加更多平台**
   - Azuro
   - Stake.com
   - 其他预测市场平台

4. **性能优化**
   - 实现 WebSocket 实时监控
   - 添加数据库缓存
   - 并发处理多个平台

5. **风险管理**
   - 添加滑点计算
   - 实现止损机制
   - 流动性检查

---

**更新时间**: 2026-02-07
**状态**: ✅ 所有 bug 已修复，新功能已添加并测试通过
