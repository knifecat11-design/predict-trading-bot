"""
Polymarket 单次扫描测试
"""

import requests
import json
from datetime import datetime
import sys
import io

# 设置 UTF-8 编码输出
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

print("=" * 80)
print("  Polymarket 套利机会扫描")
print("=" * 80)
print()

# 获取市场
print("[1] 获取活跃市场...")
url = "https://gamma-api.polymarket.com/markets"
params = {'closed': 'false', 'active': 'true', 'limit': 100}

resp = requests.get(url, params=params, timeout=15)
print(f"    状态: {resp.status_code}")

if resp.status_code != 200:
    print(f"    错误: {resp.text[:200]}")
    exit(1)

markets = resp.json()
print(f"    获取市场: {len(markets)} 个")
print()

# 分析套利机会
print("[2] 分析套利机会...")
print()

opportunities = []

for market in markets:
    try:
        # 解析价格
        outcome_prices_str = market.get('outcomePrices', '[]')
        if isinstance(outcome_prices_str, str):
            outcome_prices = json.loads(outcome_prices_str)
        else:
            outcome_prices = outcome_prices_str

        if len(outcome_prices) < 2:
            continue

        yes_price = float(outcome_prices[0])
        no_price = float(outcome_prices[1])

        # 检查价格有效性
        if yes_price <= 0 or no_price <= 0:
            continue

        # 计算套利
        combined = yes_price + no_price
        arbitrage = (1.0 - combined) * 100

        # 只保留有套利空间的
        if arbitrage > 0:
            liquidity = float(market.get('liquidity', 0) or 0)

            opportunities.append({
                'question': market.get('question', 'Unknown')[:70],
                'yes_price': yes_price,
                'no_price': no_price,
                'combined': combined,
                'arbitrage': arbitrage,
                'liquidity': liquidity,
                'volume_24h': float(market.get('volume24hr', 0) or 0)
            })
    except:
        continue

# 显示结果
print(f"    分析完成: 扫描了 {len(markets)} 个市场")
print(f"    发现机会: {len(opportunities)} 个")
print()

if opportunities:
    # 排序
    opportunities.sort(key=lambda x: x['arbitrage'], reverse=True)

    print("=" * 80)
    print("  套利机会列表")
    print("=" * 80)
    print()

    for i, opp in enumerate(opportunities[:20], 1):
        print(f"[{i}] {opp['question']}")
        print(f"    Yes: {opp['yes_price']*100:.2f}c  |  No: {opp['no_price']*100:.2f}c")
        print(f"    组合: {opp['combined']*100:.2f}c  |  套利: {opp['arbitrage']:.2f}%")
        print(f"    流动性: ${opp['liquidity']:,.0f}  |  24h: ${opp['volume_24h']:,.0f}")
        print()

    # 统计
    print("=" * 80)
    print(f"  总计: {len(opportunities)} 个套利机会")
    print(f"  平均套利空间: {sum(o['arbitrage'] for o in opportunities) / len(opportunities):.2f}%")
    print(f"  最大套利空间: {opportunities[0]['arbitrage']:.2f}%")
    print("=" * 80)
else:
    print("    未发现套利机会")
    print()
    print("    说明:")
    print("    - Polymarket 市场通常定价有效")
    print("    - Yes + No 价格总和接近 $1.00")
    print("    - 套利机会较少但可能存在")

print()
print(f"扫描时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
