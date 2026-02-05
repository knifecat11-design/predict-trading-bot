# Railway 云服务器部署指南

## 🚀 快速部署步骤

### 第一步：注册 Railway

1. 访问 https://railway.app/
2. 点击 "Start Coding" 或 "Sign Up"
3. 使用 GitHub 账号登录（推荐）

### 第二步：创建 GitHub 仓库

1. 访问 https://github.com/new
2. 创建新仓库，命名为 `predict-trading-bot`
3. **不要**勾选 "Add a README file"（我们已有代码）
4. 点击 "Create repository"

### 第三步：上传代码到 GitHub

#### 方式 A：使用 GitHub Desktop（推荐新手）
1. 下载安装 GitHub Desktop
2. 登录你的 GitHub 账号
3. File → Add Local Repository
4. 选择 `C:\Users\Administrator\predict-trading-bot`
5. Publish repository → 选择刚才创建的仓库

#### 方式 B：使用命令行
在项目目录打开 PowerShell：

```powershell
cd C:\Users\Administrator\predict-trading-bot

# 初始化 git
git init

# 添加所有文件
git add .

# 提交
git commit -m "Initial commit"

# 添加远程仓库（替换 YOUR_USERNAME）
git remote add origin https://github.com/YOUR_USERNAME/predict-trading-bot.git

# 推送代码
git branch -M main
git push -u origin main
```

### 第四步：在 Railway 部署

1. 登录 Railway 后，点击 "New Project"
2. 选择 "Deploy from GitHub repo"
3. 选择 `predict-trading-bot` 仓库
4. Railway 会自动检测 Python 项目
5. 点击 "Deploy"

**配置环境变量**（重要！）：

部署后，点击项目 → Variables → Add Variable：

```yaml
# Telegram 配置
TELEGRAM_BOT_TOKEN=8273809449:AAHKO7J_gcNxBpTvc6X_SGWGIZwKKjc4H3Q
TELEGRAM_CHAT_ID=7944527195

# Predict API
PREDICT_API_KEY=1b0c25d4-8ca6-4aa8-8910-cd72b311e4f6

# 套利配置（可选）
MIN_ARBITRAGE_THRESHOLD=2.0
SCAN_INTERVAL=10
COOLDOWN_MINUTES=5
```

### 第五步：验证部署

1. Railway 部署完成后，点击 "View Logs"
2. 检查是否有错误
3. 你应该收到 Telegram 测试消息

## 📊 监控运行状态

在 Railway 控制台：
- **Logs**：查看实时日志
- **Metrics**：查看 CPU、内存使用
- **Deploys**：查看部署历史

## 💰 费用说明

Railway 免费套餐：
- ✅ $5/月 免费额度
- ✅ 足够运行套利监控
- ✅ 超出后暂停（不会意外扣费）

## ⚙️ 常见问题

### Q: 如何重启服务？
A: 在 Railway 控制台点击 "Restart" 按钮

### Q: 如何更新代码？
A:
```powershell
git add .
git commit -m "Update code"
git push
```
Railway 会自动重新部署

### Q: 如何停止服务？
A: 在 Railway 项目页面点击 "Pause"

### Q: 可以监控多个市场吗？
A: 可以，修改 `src/arbitrage_monitor.py` 中的市场映射

## 🔄 更新代码后

```powershell
cd C:\Users\Administrator\predict-trading-bot
git add .
git commit -m "Your update message"
git push
```

Railway 会自动检测并重新部署！

---

**需要帮助？**
Railway 文档：https://docs.railway.app/
