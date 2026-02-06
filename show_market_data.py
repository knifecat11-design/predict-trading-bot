"""
显示 Polymarket 市场数据（验证数据正确性）
"""

import requests
import json
from datetime import datetime

print("=" * 80)
print("  Polymarket 市场数据展示")
print("=" * 80)
print()

# 获取市场
url = "https://gamma-api.polymarket.com/markets"
params = {'closed': 'false', 'active': 'true', 'limit': 100}

resp = requests.get(url, params=params, timeout=15)
markets = resp.json()

print(f"获取市场: {len(markets)} 个活跃市场")
print()

print("=" * 80)
print("  前 20 个市场详情")
print("=" * 80)
print()

for i, market in enumerate(markets[:20], 1):
    try:
        question = market.get('question', 'Unknown')
        outcome_prices_str = market.get('outcomePrices', '[]')

        if isinstance(outcome_prices_str, str):
            outcome_prices = json.loads(outcome_prices_str)
        else:
            outcome_prices = outcome_prices_str

        if len(outcome_prices) >= 2:
            yes_price = float(outcome_prices[0])
            no_price = float(outcome_prices[1])
            combined = yes_price + no_price
            arbitrage = (1.0 - combined) * 100

            liquidity = float(market.get('liquidity', 0) or 0)
            volume_24h = float(market.get('volume24hr', 0) or 0)

            print(f"[{i:2d}] {question[:65]}")
            print(f"     Yes: {yes_price*100:6.2f}c  No: {no_price*100:6.2f}c  组合: {combined*100:6.2f}c  套利: {arbitrage:+6.2f}%")
            print(f"     流动性: ${liquidity:8,.0f}  24h成交: ${volume_24h:8,.0f}")
            print()

    except Exception as e:
        continue

# 统计分析
print("=" * 80)
print("  统计分析")
print("=" * 80)
print()

total_combined = 0
count = 0
arbitrage_count = 0
positive_arbitrage_count = 0

for market in markets:
    try:
        outcome_prices_str = market.get('outcomePrices', '[]')
        if isinstance(outcome_prices_str, str):
            outcome_prices = json.loads(outcome_prices_str)
        else:
            outcome_prices = outcome_prices_str

        if len(outcome_prices) >= 2:
            yes_price = float(outcome_prices[0])
            no_price = float(outcome_prices[1])
            combined = yes_price + no_price
            arbitrage = (1.0 - combined) * 100

            total_combined += combined
            count += 1

            if abs(arbitrage) > 0.01:
                arbitrage_count += 1
            if arbitrage > 0:
                positive_arbitrage_count += 1
    except:
        continue

if count > 0:
    avg_combined = total_combined / count
    avg_arbitrage = (1.0 - avg_combined) * 100

    print(f"总市场数: {count}")
    print(f"平均组合价格: {avg_combined*100:.2f}c")
    print(f"平均套利空间: {avg_arbitrage:+.2f}%")
    print(f"有明显套利(>0.01%)的市场: {arbitrage_count} ({arbitrage_count/count*100:.1f}%)")
    print(f"正套利空间的市场: {positive_arbitrage_count} ({positive_arbitrage_count/count*100:.1f}%)")

print()
print("=" * 80)
print(f"扫描时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 80)
