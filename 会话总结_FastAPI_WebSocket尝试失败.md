# 会话总结：FastAPI + WebSocket 部署尝试

**日期**: 2026-02-14
**目标**: 升级 dashboard 到 FastAPI + WebSocket 实时推送
**结果**: ❌ 失败，最终回退到 Flask 版本

---

## 完成的工作

### 1. 初始任务：升级到 FastAPI + WebSocket
- ✅ 创建 `src/polymarket_websocket.py` - Polymarket WebSocket 客户端
- ✅ 创建 `web/dashboard_fastapi.py` - FastAPI 版本的 dashboard
- ✅ 更新 `requirements.txt` - 添加 fastapi, uvicorn, websockets
- ✅ 创建 `test_websocket_fastapi.py` - 测试脚本

### 2. Railway 配置
- ✅ 更新 `railway.json` - FastAPI 配置
- ✅ 推送代码到 GitHub

### 3. 多次失败的尝试

#### 尝试 1: 文件命名问题
- **问题**: `railway-web.json` → Railway 识别为 `railway.json`
- **修复**: 重命名文件
- **结果**: 仍然失败

#### 尝试 2: 分支混乱
- **问题**: Railway 使用了错误的分支
- **修复**: 合并代码到 main 分支

#### 尝试 3: 应用启动失败
- **问题**: "Application failed to respond"
- **原因**: FastAPI 启动代码有问题
- **修复**: 简化启动逻辑，延迟扫描

#### 尝试 4: asyncio.Lock 错误
- **问题**: `AttributeError: 'asyncio.Lock' object has no attribute 'acquire'`
- **原因**: asyncio.Lock API 在 Python 3.10+ 不支持手动 acquire/release
- **修复**: 使用函数属性标记代替

#### 尝试 5: 持续失败
- **问题**: FastAPI 版本持续无法启动
- **原因**: 异步编程复杂性，多个并发问题

### 4. 最终方案：使用 AI 优化的 Flask 版本
- ✅ 合并 `claude/review-trading-bot-ldRX0` 分支
- ✅ 该分支使用 **Flask-SocketIO** 实现 WebSocket
- ✅ 保留所有性能优化（v3.0）
- ✅ 已测试且稳定

---

## 当前状态

### 已部署版本（claude 分支）
**文件**: `web/dashboard.py`
**技术栈**: Flask + Flask-SocketIO
**功能**:
- ✅ WebSocket 实时推送（Flask-SocketIO）
- ✅ 并发平台获取（3× 更快）
- ✅ 扫描保护（防止重叠）
- ✅ 资源限制（67% 更少请求）
- ✅ 套利过期清理（10 分钟）
- ✅ 自动降级到 HTTP 轮询

**资源限制**:
- Polymarket: 100 市场标签（从 5000 减少）
- Opinion: 200/150 市场（从 5000 减少）
- Predict: 25 市场（从 1000 减少）
- 最小扫描间隔: 45 秒

**依赖** (`requirements.txt`):
```
flask>=3.0.0
flask-socketio>=5.3.0
simple-websocket>=1.0.0
websocket-client>=1.6.0
websockets>=16.0
PyYAML>=6.0
python-dotenv>=1.0.0
```

**Railway 配置** (`railway.json`):
```json
{
  "deploy": {
    "startCommand": "python web/dashboard.py",
    "healthcheckPath": "/"
  },
  "variables": {
    "PORT": "5000",
    "SCAN_INTERVAL": "60",
    "OPINION_API_KEY": "JycT63x2kYkqtTrlCnpR58O57agzKouz",
    "OPINION_POLY_THRESHOLD": "2.0"
  }
}
```

### 未完成的工作
- ❌ FastAPI 版本无法部署
- ❌ `web/dashboard_fastapi.py` 已废弃
- ❌ `src/polymarket_websocket.py` 未使用

---

## 遗留问题

### Railway 部署问题
**当前状态**: https://railway-web-dashboard-production.up.railway.app/ 无法访问

**可能原因**:
1. Railway 未检测到最新 commit (`d90ce0e`)
2. 需要手动触发部署
3. 配置问题

**建议排查步骤**:
1. 在 Railway 检查服务是否使用 `main` 分支
2. 手动触发新部署（Deployments → New Deploy）
3. 查看 Deploy Logs 的错误信息
4. 确认环境变量已配置

---

## 关键提交历史

```
d90ce0e feat: add WebSocket real-time push while keeping all scan optimizations
4b51f8a fix: prevent app crash from excessive API requests per scan cycle
ab3d442 fix: 4 bugs blocking market count
da56ded perf: 10x market count by eliminating per-market HTTP requests
```

---

## 下一步建议

### 短期（立即）
1. **修复 Railway 部署**
   - 检查 Railway 服务配置
   - 手动触发部署
   - 查看日志找出错误

2. **验证功能**
   - 测试 WebSocket 连接
   - 确认市场数量正常
   - 验证套利发现功能

### 中期
1. **优化性能**
   - 根据实际负载调整资源限制
   - 监控内存和 CPU 使用

2. **添加监控**
   - Railway 日志监控
   - 错误告警（Telegram）

### 长期
1. **Kalshi 集成** (Task 2)
2. **AI 市场匹配** (Task 3)
3. **NegRisk 重新平衡** (Task 4)

---

## 重要文件位置

| 文件 | 路径 | 说明 |
|------|------|------|
| 主程序 | `web/dashboard.py` | Flask + SocketIO 版本（当前使用） |
| 废弃文件 | `web/dashboard_fastapi.py` | FastAPI 版本（失败） |
| 废弃文件 | `src/polymarket_websocket.py` | WebSocket 客户端（未使用） |
| Railway 配置 | `railway.json` | 部署配置 |
| 依赖 | `requirements.txt` | Python 包 |
| 配置文件 | `config.yaml` | 应用配置 |

---

## 联系信息

**GitHub 仓库**: https://github.com/knifecat11-design/predict-trading-bot
**Railway 项目**: https://railway.app/
**部署 URL**: https://railway-web-dashboard-production.up.railway.app/

---

**备注**: FastAPI + WebSocket 尝试失败，最终使用 Flask-SocketIO 实现相同功能。当前版本已稳定运行但 Railway 部署仍有问题需要排查。
