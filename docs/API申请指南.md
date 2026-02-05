# API 申请指南

本文档说明如何申请 Polymarket 和 Predict.fun 的 API 访问权限。

---

## 📊 Polymarket API

### 状态：✅ 公开访问，无需申请

Polymarket Gamma API 是公开的，**无需 API Key** 即可访问市场数据。

### 基础信息

- **Base URL**: `https://gamma-api.polymarket.com`
- **文档**: https://docs.polymarket.com/developers/gamma-markets-api/overview
- **端点参考**: https://docs.polymarket.com/quickstart/reference/endpoints

### 主要端点

| 端点 | 描述 | 认证 |
|------|------|------|
| `/markets` | 获取市场列表 | 无需认证 |
| `/markets/{id}` | 获取单个市场详情 | 无需认证 |
| `/status` | API 健康检查 | 无需认证 |

### 使用示例

```bash
# 获取市场列表
curl "https://gamma-api.polymarket.com/markets?limit=100"

# 健康检查
curl "https://gamma-api.polymarket.com/status"
```

### Python 示例

```python
import requests

# 获取市场数据
response = requests.get("https://gamma-api.polymarket.com/markets", params={"limit": 10})
markets = response.json()

for market in markets:
    print(f"Market: {market['question']}")
    print(f"Price: {market.get('price', 'N/A')}")
```

---

## 🎲 Predict.fun API

### 状态：❌ 需要 API Key

Predict.fun API 需要通过 Discord 申请访问权限。

### 基础信息

- **Base URL**: `https://api.predict.fun`
- **API 版本**: `v1`
- **文档**: https://dev.predict.fun/
- **Swagger**: https://api.predict.fun/docs
- **通用信息**: https://dev.predict.fun/general-information-1915499m0

### 如何申请 API Key

1. **加入 Discord 服务器**
   - 访问 https://dev.predict.fun/
   - 点击 Discord 邀请链接加入服务器

2. **申请 API 访问**
   - 在 Discord 中开一个 support ticket
   - 说明您需要 API 访问权限
   - 等待团队审核并发放 API Key

3. **配置 API Key**
   - 获得 API Key 后，在 Railway 环境变量中设置：
     ```
     PREDICT_API_KEY=你的API密钥
     ```

### 主要端点（需要认证）

| 端点 | 描述 | 认证 |
|------|------|------|
| `/v1/markets` | 获取市场列表 | 需要 API Key |
| `/v1/markets/{id}/orderbook` | 获取订单簿 | 需要 API Key |
| `/v1/orders` | 下单/查询订单 | 需要 API Key |

### 使用示例

```python
import requests

api_key = "你的API密钥"

# ✅ 正确的认证方式：使用 x-api-key header
headers = {
    "x-api-key": api_key,  # 注意：使用 x-api-key，不是 Authorization: Bearer
    "Content-Type": "application/json"
}

# 获取市场列表
response = requests.get(
    "https://api.predict.fun/v1/markets",
    headers=headers,
    params={"active": True}
)

if response.status_code == 200:
    markets = response.json()
    print(f"获取到 {len(markets)} 个市场")
elif response.status_code == 401:
    print("认证失败：请检查 API Key 是否正确")
```

**⚠️ 重要提示**：
- ❌ 不要使用 `Authorization: Bearer {api_key}`
- ✅ 应该使用 `x-api-key: {api_key}`
- 对于公共端点（如 `/v1/markets`），只需要 API Key
- 对于私有操作（如下单），还需要 JWT Token（需要钱包签名）

---

## 🚀 启用真实 API

### 步骤 1: 申请 Predict.fun API Key

按照上面的说明在 Discord 申请。

### 步骤 2: 配置环境变量

在 Railway 项目设置中添加环境变量：

```bash
# 启用真实 API
USE_REAL_API=true

# Predict.fun API Key（从 Discord 获取）
PREDICT_API_KEY=你的实际API密钥
```

### 步骤 3: 推送并重新部署

```powershell
cd C:\Users\Administrator\predict-trading-bot
git add .
git commit -m "Enable real API mode"
git push
```

Railway 会自动重新部署。

---

## 📝 参考

### Polymarket 资源

- [官方文档](https://docs.polymarket.com/)
- [Gamma API 概述](https://docs.polymarket.com/developers/gamma-markets-api/overview)
- [如何获取市场数据](https://docs.polymarket.com/developers/gamma-markets-api/fetch-markets-guide)
- [Python SDK](https://pypi.org/project/polymarket-apis/)

### Predict.fun 资源

- [开发者文档](https://dev.predict.fun/)
- [API 文档 (Swagger)](https://api.predict.fun/docs)
- [连接账户指南](https://dev.predict.fun/get-connected-account-25326917e0)

---

## ⚠️ 注意事项

1. **Polymarket**:
   - 无需 API Key 即可读取公开市场数据
   - 如需交易功能，需要使用 CLOB API 并配置签名

2. **Predict.fun**:
   - 必须通过 Discord 申请 API Key
   - 未授权的请求会返回 `401 Unauthorized`

3. **当前程序状态**:
   - 默认使用模拟数据（`USE_REAL_API=false`）
   - 切换到真实 API 前请确保已获得必要权限

---

**创建日期**: 2026-02-05
**最后更新**: 2026-02-05
