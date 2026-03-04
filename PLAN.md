# Polymarket 争议信号系统 — 实施计划

## 背景

Polymarket 使用 UMA Optimistic Oracle 来结算市场。当提案被争议时，意味着有人支付了 $500+ 保证金来挑战结果——这是一个高价值信号。如果 Oracle 结算价格与当前市场价格存在重大偏差，可能存在套利或风险规避的机会。

### 数据来源（已验证可用）

| 子图 | 端点 | 用途 |
|------|------|------|
| OOv2 (Polygon) | `https://api.goldsky.com/.../polygon-optimistic-oracle-v2/1.1.0/gn` | 旧合约争议 |
| Managed OOv2 (Polygon) | `https://api.goldsky.com/.../polygon-managed-optimistic-oracle-v2/1.0.5/gn` | **当前活跃**合约争议 |

### 关键 Schema: `OptimisticPriceRequest`

```
id, identifier, ancillaryData (含 market_id, 标题, 描述),
proposer, proposedPrice (0=No, 1e18=Yes),
disputer, disputeTimestamp,
state: [Requested, Proposed, Disputed, Resolved, Settled],
settlementPrice, bond
```

### 信号类型（按价值排序）

1. **新争议信号**: 提案被争议（`state=Disputed`）→ 即时通知
2. **结算反转信号**: DVM投票结果 ≠ 原提案（`settlementPrice ≠ proposedPrice`）→ 重大反转
3. **Oracle vs 市场价格偏差**: Oracle 提议 Yes 但市场价格 < 30%（或反之）→ 预警信号
4. **提案待挑战信号**: 新提案仍在挑战期，且与市场价格相矛盾 → 潜在争议

---

## 实施步骤

### Phase 1: UMA Oracle API 客户端 (`src/uma_oracle_api.py`)

新建文件，遵循现有 API 客户端模式（class-based, requests.Session, 超时, 缓存）：

- `UMAOracleClient` 类
  - `__init__(config)`: 初始化 GraphQL 端点（OOv2 + MOOV2）
  - `query_disputes(first, skip)`: 查询有争议的 OptimisticPriceRequest
  - `query_recent_proposals(first)`: 查询最近提案（含挑战期内的）
  - `query_settled_requests(first)`: 查询已结算的请求
  - `_decode_ancillary_data(hex_str)`: 解码 hex ancillaryData 为 UTF-8 文本
  - `_extract_market_info(ancillary_text)`: 从文本提取 market_id, title, res_data
  - `_parse_proposed_price(price_wei)`: 将 wei 价格转换为可读结果（Yes/No/Unknown）

- 数据模型 `@dataclass`:
  - `OracleRequest`: request_id, market_id, title, proposed_outcome, dispute_status, disputer, settlement_outcome, timestamps, bond 等

- 缓存策略: 60秒 TTL（争议事件不会太频繁）
- 同时查询 OOv2 和 MOOV2 端点，合并去重

### Phase 2: 争议信号检测引擎 (`src/dispute_signal.py`)

新建文件，核心检测逻辑：

- `DisputeSignalDetector` 类
  - `__init__(uma_client, poly_client, config)`: 注入 UMA 和 Polymarket 客户端
  - `detect_signals()` → `List[DisputeSignal]`: 主检测方法
  - `_check_new_disputes()`: 检测新争议（对比上次扫描状态）
  - `_check_settlement_reversals()`: 检测结算反转
  - `_check_oracle_market_divergence()`: 检测 Oracle 结果 vs 市场价格偏差
  - `_check_pending_proposals()`: 检测挑战期内的可疑提案
  - `_match_oracle_to_market(market_id)`: 通过 market_id 关联 Polymarket 市场获取当前价格

- 数据模型:
  - `DisputeSignal`: signal_type, severity(HIGH/MEDIUM/LOW), market_id, title, oracle_outcome, market_price, divergence_pct, timestamps, details

- 状态跟踪:
  - 已通知的争议 ID 集合（避免重复通知）
  - 上次扫描的 snapshot（用于检测新事件）

### Phase 3: Telegram 通知集成

在 `continuous_monitor.py` 中集成：

- 新增争议信号扫描步骤到主循环
- 复用现有 `send_telegram()` 函数
- 新增 `format_dispute_signal_message(signal)`: 格式化争议信号消息
  - 包含: 信号类型、严重度、市场标题、Oracle 提议结果、当前市场价格、偏差百分比、争议详情
- 通知策略:
  - HIGH（新争议/结算反转）: 立即通知
  - MEDIUM（Oracle vs 市场偏差 > 20%）: 30分钟冷却
  - LOW（挑战期内提案）: 1小时冷却

### Phase 4: 配置与环境变量

在 `src/config_helper.py` 中新增：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DISPUTE_SIGNAL_ENABLED` | `true` | 启用争议信号检测 |
| `DISPUTE_SCAN_INTERVAL` | `120` | 争议扫描间隔（秒） |
| `DISPUTE_DIVERGENCE_THRESHOLD` | `20.0` | Oracle vs 市场偏差阈值（%） |
| `DISPUTE_COOLDOWN_MINUTES` | `30` | 通知冷却时间（分钟） |

更新 `config.yaml.example` 和 `.env.example`。

### Phase 5: Dashboard 集成（可选，后续）

在 `web/dashboard.py` 中：
- 新增争议信号面板
- 实时推送争议事件到前端
- 显示争议历史和状态变化

---

## 文件变更列表

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `src/uma_oracle_api.py` | UMA Oracle GraphQL 客户端 |
| 新建 | `src/dispute_signal.py` | 争议信号检测引擎 |
| 修改 | `continuous_monitor.py` | 集成争议信号到主循环 |
| 修改 | `src/config_helper.py` | 新增争议相关配置 |
| 修改 | `config.yaml.example` | 新增争议配置模板 |
| 修改 | `.env.example` | 新增争议环境变量 |
| 修改 | `requirements.txt` | 无需新增（使用 requests） |

## 技术要点

- GraphQL 查询使用 `requests.post()`，无需额外依赖
- `ancillaryData` 为 hex 编码的 UTF-8，包含 `market_id: XXXX`，用正则提取
- `proposedPrice`: `0` = No, `1000000000000000000` (1e18) = Yes, `500000000000000000` (5e17) = Unknown
- 同时监控 OOv2 和 MOOV2 两个子图端点
- Goldsky 速率限制: 50 req/10s（足够）
