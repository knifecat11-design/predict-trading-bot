# Railway 部署配置指南

## 服务列表

### 1. web-dashboard (Web 界面)
**用途**: FastAPI Dashboard + WebSocket 实时监控
**启动命令**: `python web/dashboard_fastapi.py`
**端口**: 8000
**健康检查**: `/health`

**Railway 配置**:
- GitHub 仓库: `knifecat11-design/predict-trading-bot`
- 分支: `railway-web-dashboard` 或 `main`
- 根目录: `/`
- 启动命令: `python web/dashboard_fastapi.py`
- 环境变量:
  ```
  PORT=8000
  PREDICT_API_KEY=
  OPINION_API_KEY=JycT63x2kYkqtTrlCnpR58O57agzKouz
  OPINION_WALLET_ADDRESS=0x138fad69eb759f4460de2e3fb79173b034142021
  OPINION_POLY_THRESHOLD=2.0
  SCAN_INTERVAL=30
  LOG_LEVEL=INFO
  ```

**部署后访问**: https://web-dashboard-xxx.up.railway.app

### 2. py-web-dashboard (套利监控)
**用途**: 后台持续扫描套利机会
**启动命令**: `python start_arbitrage.py`
**配置文件**: `railway-monitor.json`

**环境变量**:
```
USE_REAL_API=false
USE_HYBRID_MODE=true
MIN_ARBITRAGE_THRESHOLD=2.0
OPINION_API_KEY=JycT63x2kYkqtTrlCnpR58O57agzKouz
OPINION_WALLET_ADDRESS=0x138fad69eb759f4460de2e3fb79173b034142021
OPINION_POLY_THRESHOLD=2.0
SCAN_INTERVAL=10
COOLDOWN_MINUTES=5
LOG_LEVEL=INFO
TELEGRAM_BOT_TOKEN=8273809449:AAHKO7J_gcNxBpTvc6X_SGWGIZwKKjc4H3Q
TELEGRAM_CHAT_ID=7944527195
```

## 如何重新配置 web-dashboard 服务

### 方法 1: 删除服务重新创建（推荐）

1. 在 Railway 项目中删除 `web-dashboard` 服务
2. 点击 **"New Service"**
3. 选择 **"Deploy from GitHub repo"**
4. 选择 `knifecat11-design/predict-trading-bot`
5. 选择分支 `railway-web-dashboard`
6. Railway 会自动读取 `railway.json` 配置
7. 等待部署完成（2-3分钟）

### 方法 2: 手动更新现有服务

1. 点击 `web-dashboard` 服务
2. 进入 **Settings** 标签
3. 修改以下配置：
   - **Root Directory**: `/`
   - **Start Command**: `python web/dashboard_fastapi.py`
4. 进入 **Variables** 标签
5. 设置环境变量（参考上面的值）
6. 保存后 Railway 会自动重新部署

## 验证部署

部署成功后，在日志中应该看到：

```
INFO:     Started server process [1]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

而不是 Flask 的 `werkzeug` 日志。

## 快速测试

```bash
# 健康检查
curl https://web-dashboard-xxx.up.railway.app/health

# 获取市场状态
curl https://web-dashboard-xxx.up.railway.app/api/state
```

## WebSocket 连接

浏览器控制台测试：

```javascript
const ws = new WebSocket('wss://web-dashboard-xxx.up.railway.app/ws');

ws.onopen = () => {
  ws.send(JSON.stringify({
    type: 'subscribe',
    channels: ['prices', 'arbitrage', 'scan']
  }));
};

ws.onmessage = (event) => {
  console.log('收到消息:', JSON.parse(event.data));
};
```
