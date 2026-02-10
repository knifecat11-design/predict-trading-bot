"""
Opinion API 诊断脚本 - 用于 Railway 调试
"""
import os
import sys

print("=" * 60)
print("  Opinion API 诊断工具")
print("=" * 60)

# 1. 检查环境变量
print("\n[1] 环境变量检查")
api_key = os.getenv('OPINION_API_KEY', '')
print(f"OPINION_API_KEY: {'SET' if api_key else 'NOT SET'}")
if api_key:
    print(f"  长度: {len(api_key)}")
    print(f"  前10位: {api_key[:10]}")

# 2. 测试配置加载
print("\n[2] 配置加载测试")
try:
    from src.config_helper import load_config
    config = load_config()
    opinion_config = config.get('opinion', {})
    print(f"opinion.api_key: {'SET' if opinion_config.get('api_key') else 'MISSING'}")
    print(f"opinion.base_url: {opinion_config.get('base_url', 'MISSING')}")
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)

# 3. 测试 API 连接
print("\n[3] API 连接测试")
try:
    import requests
    base_url = opinion_config.get('base_url', 'https://proxy.opinion.trade:8443/openapi')

    # 测试不同的认证方式
    test_cases = [
        ('apikey header', {'apikey': api_key, 'Content-Type': 'application/json'}),
        ('Authorization Bearer', {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}),
    ]

    for name, headers in test_cases:
        print(f"\n  测试: {name}")
        try:
            response = requests.get(
                f"{base_url}/market",
                headers=headers,
                params={'limit': 1},
                timeout=10
            )
            print(f"  Status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                result = data.get('result', {})
                lst = result.get('list', []) if isinstance(result, dict) else []
                print(f"  Markets: {len(lst)}")
                if lst:
                    print(f"  第一个市场: {lst[0].get('marketTitle', 'N/A')[:40]}")
                    break  # 找到工作的方式就停止
            else:
                print(f"  Error: {response.text[:100]}")
        except Exception as e:
            print(f"  Exception: {e}")

    # 4. 测试 OpinionClient 初始化
    print("\n[4] OpinionClient 初始化测试")
    from src.opinion_api import OpinionAPIClient

    client = OpinionAPIClient(config)
    print(f"  API Key 设置: {bool(client.api_key)}")
    print(f"  Base URL: {client.base_url}")
    print(f"  使用 SDK: {client._use_sdk}")
    print(f"  Session 存在: {hasattr(client, 'session')}")

    # 5. 测试获取市场
    print("\n[5] 获取市场测试")
    try:
        markets = client.get_markets(status='activated', limit=10)
        print(f"  获取到 {len(markets)} 个市场")

        if markets:
            print(f"  第一个市场: {markets[0].get('marketTitle', 'N/A')[:50]}")
            token_id = markets[0].get('yesTokenId', '')
            print(f"  Token ID: {token_id[:30]}...")

            # 6. 测试获取价格
            print("\n[6] 获取价格测试")
            if token_id:
                price = client.get_token_price(token_id)
                print(f"  Token 价格: {price}")
                if price:
                    print("\n" + "=" * 60)
                    print("  SUCCESS: Opinion API 完全正常!")
                    print("=" * 60)
                else:
                    print("\n  ERROR: 价格获取失败!")
            else:
                print("  ERROR: Token ID 为空!")
        else:
            print("  ERROR: 未获取到任何市场!")
            print("\n  可能原因:")
            print("  1. API Key 无效")
            print("  2. 网络连接问题")
            print("  3. API 端点变更")

    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()

except Exception as e:
    print(f"\nFATAL ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("  诊断完成")
print("=" * 60)
