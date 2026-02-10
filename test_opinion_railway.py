"""
Railway Opinion API 快速测试
"""
import os
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

print("=" * 60)
print("  Railway Opinion API 测试")
print("=" * 60)

# 1. 环境变量
print("\n[环境变量]")
api_key = os.getenv('OPINION_API_KEY', '')
print(f"OPINION_API_KEY: {'SET' if api_key else 'MISSING'}")
if api_key:
    print(f"  Key: {api_key[:10]}...{api_key[-4:]}")

# 2. 加载配置
print("\n[配置加载]")
try:
    from src.config_helper import load_config
    config = load_config()
    opinion_config = config.get('opinion', {})
    print(f"opinion.api_key: {'SET' if opinion_config.get('api_key') else 'MISSING'}")
    print(f"opinion.base_url: {opinion_config.get('base_url')}")
except Exception as e:
    print(f"ERROR: {e}")
    exit(1)

# 3. 初始化客户端
print("\n[客户端初始化]")
try:
    from src.opinion_api import OpinionAPIClient
    client = OpinionAPIClient(config)
    print(f"API Key: {bool(client.api_key)}")
    print(f"Base URL: {client.base_url}")
    print(f"Use SDK: {client._use_sdk}")
    print(f"Has Session: {hasattr(client, 'session')}")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

# 4. 获取市场
print("\n[获取市场]")
try:
    markets = client.get_markets(status='activated', limit=10)
    print(f"成功获取 {len(markets)} 个市场")

    if markets:
        print(f"\n第一个市场:")
        print(f"  ID: {markets[0].get('marketId')}")
        print(f"  标题: {markets[0].get('marketTitle', 'N/A')[:50]}")

        token_id = markets[0].get('yesTokenId', '')
        if token_id:
            print(f"  Token: {token_id[:30]}...")

            # 5. 获取价格
            print("\n[获取价格]")
            price = client.get_token_price(token_id)
            print(f"Token 价格: {price}")

            if price:
                print("\n" + "=" * 60)
                print("  SUCCESS: Opinion API 在 Railway 上完全正常!")
                print("=" * 60)
                exit(0)
            else:
                print("\nERROR: 价格获取失败!")
                exit(1)
    else:
        print("\nERROR: 未获取到任何市场!")
        print("\n可能原因:")
        print("1. API Key 无效")
        print("2. 网络连接问题")
        print("3. API 端点变更")
        exit(1)

except Exception as e:
    print(f"\nERROR: {e}")
    import traceback
    traceback.print_exc()
    exit(1)
