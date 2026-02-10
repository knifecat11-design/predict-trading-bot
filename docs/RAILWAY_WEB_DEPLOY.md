# Railway Web 面板部署指南

## 架构说明

项目使用 **2 个独立的 Railway Service**：

| Service | 启动命令 | 功能 | 端口 |
|---------|----------|------|------|
| **Monitor Bot** | `python start_arbitrage.py` | 套利监控 + TG 推送 | 无（后台运行） |
| **Web Dashboard** | `python web/dashboard.py` | Web 监控面板 | 5000 |

---

## 部署步骤

### 1. 推送代码到 GitHub

```bash
git push origin main
```

### 2. 在 Railway 创建 Web 面板 Service

1. 登录 [Railway Dashboard](https://railway.app/)
2. 进入你的项目 `predict-trading-bot`
3. 点击 **"+ New Service"** → **"Deploy from GitHub repo"**
4. 选择 `knifecat11-design/predict-trading-bot`
5. 重要：选择 **`main`** 分支（不是 PR 分支）

### 3. 配置 Web 面板 Service

#### 修改启动命令
Settings → General → Start Command:
```
python web/dashboard.py
```

#### 添加环境变量
Settings → Variables:

| Key | Value |
|-----|-------|
| `PORT` | `5000` |
| `OPINION_API_KEY` | `JycT63x2kYkqtTrlCnpR58O57agzKouz` |
| `OPINION_WALLET_ADDRESS` | `0x138fad69eb759f4460de2e3fb79173b034142021` |
| `OPINION_POLY_THRESHOLD` | `2.0` |
| `SCAN_INTERVAL` | `30` |
| `LOG_LEVEL` | `INFO` |

#### 生成公开域名
Settings → Networking → **"Generate Domain"**

复制生成的 URL，格式如：
```
https://predict-trading-bot-web.up.railway.app
```

---

## 验证部署

### 1. 检查 Service 状态

在 Railway Dashboard 确认：
- Monitor Bot: Running
- Web Dashboard: Running

### 2. 访问 Web 面板

打开生成的域名，应该看到：
- 三平台状态卡片
- 套利机会表格
- 市场列表

### 3. 检查日志

点击 Service → View Logs，确认：
```
Background scanner started
Dashboard: http://0.0.0.0:5000
Scan #1: Poly=3000 Opinion=500 Predict=0 Arb=X
```

---

## 配置说明

### Web 面板 vs Monitor Bot

| 特性 | Web 面板 | Monitor Bot |
|------|----------|-------------|
| 实时数据展示 | ✅ | ❌ |
| TG 推送 | ❌ | ✅ |
| 后台持续扫描 | ✅ | ✅ |
| 公开访问 | ✅ | ❌ |
| 部署端口 | 5000 | 无 |

### 共享配置

两个 Service 共享同一套代码和配置：
- `config.yaml` - 主配置文件
- `src/` - API 客户端
- `requirements.txt` - 依赖包

---

## 故障排查

### Web 面板无法访问

1. 检查启动命令是否为 `python web/dashboard.py`
2. 确认 `PORT=5000` 环境变量已设置
3. 查看 Logs 确认服务正常启动

### 数据不更新

1. 检查 Opinion API Key 是否正确
2. 查看后台扫描日志
3. 尝试重启 Service

### Telegram 不推送

Web 面板不负责 TG 推送，这是 Monitor Bot 的功能。确认：
1. Monitor Bot Service 正在运行
2. `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID` 已配置

---

## 成功标志

部署成功后，你应该看到：

```
✅ Railway: 2 个 Service 运行中
✅ Web 面板: 正常显示市场数据
✅ Monitor Bot: 正常推送套利机会
✅ 日志: 持续扫描输出
```

---

**最后更新**: 2026-02-10
**项目状态**: ✅ 双 Service 架构就绪
