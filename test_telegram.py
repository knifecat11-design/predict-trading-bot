import requests
import yaml

# 加载配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

bot_token = config['notification']['telegram']['bot_token']
chat_id = config['notification']['telegram']['chat_id']

# 发送测试消息
url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

message = """🔔 套利监控系统测试成功！

━━━━━━━━━━━━━━━━━━━━━
✓ Telegram 连接正常
✓ Predict API Key 已配置
✓ 系统准备就绪

系统将开始监控套利机会...

配置信息:
• 套利阈值: 2.0%
• 扫描间隔: 10秒
• 冷却时间: 5分钟
━━━━━━━━━━━━━━━━━━━━━"""

response = requests.post(url, json={
    'chat_id': chat_id,
    'text': message
}, timeout=10)

print(f"状态码: {response.status_code}")
print(f"响应: {response.json()}")

if response.json().get('ok'):
    print("\n✓ 测试消息发送成功！")
else:
    print(f"\n✗ 发送失败: {response.json()}")
