"""
简化的测试程序 - 仅测试 Telegram 通知
"""
import os
import sys
import time

print("=" * 60)
print("Predict Trading Bot - Simple Test")
print("=" * 60)

# 测试环境变量
telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '8273809449:AAHKO7J_gcNxBpTvc6X_SGWGIZwKKjc4H3Q')
telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID', '7944527195')

print(f"Telegram Token: {telegram_token[:10]}...")
print(f"Chat ID: {telegram_chat_id}")

# 测试 Telegram 通知
print("\n测试 Telegram 通知...")
try:
    import requests

    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
    message = "✅ Railway 部署测试成功！\n\n系统正常运行中..."

    response = requests.post(url, json={
        'chat_id': telegram_chat_id,
        'text': message
    }, timeout=10)

    if response.json().get('ok'):
        print("✓ Telegram 通知发送成功！")
    else:
        print(f"✗ 发送失败: {response.json()}")
except Exception as e:
    print(f"✗ 错误: {e}")

print("\n" + "=" * 60)
print("测试完成，开始监控...")
print("=" * 60)

# 简单监控循环
count = 0
while True:
    count += 1
    print(f"[{count}] 系统运行中... (每30秒输出一次)")
    time.sleep(30)
