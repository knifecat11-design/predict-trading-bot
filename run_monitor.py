"""
Polymarket 实时套利监控
使用 Gamma API（正确数据源）
"""

import requests
import json
import time
from datetime import datetime

def get_active_markets(limit=50):
    """获取活跃市场"""
    url = "https://gamma-api.polymarket.com/markets"
    params = {
        'closed': 'false',
        'active': 'true',
        'limit': limit
    }

    resp = requests.get(url, params=params, timeout=15)
    if resp.status_code == 200:
        return resp.json()
    return []

def analyze_arbitrage(market):
    """分析单个市场的套利机会"""
    try:
        # 从 outcomePrices 解析价格
        outcome_prices_str = market.get('outcomePrices', '[]')
        if isinstance(outcome_prices_str, str):
            outcome_prices = json.loads(outcome_prices_str)
        else:
            outcome_prices = outcome_prices_str

        if len(outcome_prices) < 2:
            return None

        yes_price = float(outcome_prices[0])
        no_price = float(outcome_prices[1])

        # 检查价格是否有效
        if yes_price <= 0 or no_price <= 0 or yes_price >= 1 or no_price >= 1:
            return None

        # 计算套利空间
        combined = yes_price + no_price
        arbitrage = (1.0 - combined) * 100  # 转换为百分比

        # 获取市场信息
        liquidity = float(market.get('liquidity', 0) or 0)
        volume_24h = float(market.get('volume24hr', 0) or 0)

        # 只考虑有足够流动性的市场
        if liquidity < 1000:
            return None

        return {
            'question': market.get('question', 'Unknown Market')[:80],
            'yes_price': yes_price,
            'no_price': no_price,
            'combined': combined,
            'arbitrage': arbitrage,
            'liquidity': liquidity,
            'volume_24h': volume_24h,
            'condition_id': market.get('conditionId', '')[:20],
            'end_date': market.get('endDate', '')
        }

    except Exception as e:
        return None

def main():
    """主监控循环"""
    print("=" * 80)
    print("  Polymarket 实时套利监控")
    print("  数据源: Gamma API (https://gamma-api.polymarket.com)")
    print("=" * 80)
    print()

    scan_count = 0
    min_arbitrage_threshold = 0.5  # 最小套利阈值 0.5%

    while True:
        scan_count += 1
        print(f"\n[扫描 #{scan_count}] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("-" * 80)

        try:
            # 获取市场
            markets = get_active_markets(limit=100)

            if not markets:
                print("  [警告] 无法获取市场数据")
                time.sleep(30)
                continue

            print(f"  获取市场: {len(markets)} 个")

            # 分析套利机会
            opportunities = []

            for market in markets:
                result = analyze_arbitrage(market)
                if result and result['arbitrage'] > min_arbitrage_threshold:
                    opportunities.append(result)

            # 显示结果
            if opportunities:
                print(f"\n  >>> 发现 {len(opportunities)} 个套利机会 <<<\n")

                # 按套利空间排序
                opportunities.sort(key=lambda x: x['arbitrage'], reverse=True)

                for i, opp in enumerate(opportunities[:10], 1):
                    print(f"  [{i}] {opp['question']}")
                    print(f"      Yes: {opp['yes_price']*100:.2f}c  |  No: {opp['no_price']*100:.2f}c")
                    print(f"      组合: {opp['combined']*100:.2f}c  |  套利: {opp['arbitrage']:.2f}%")
                    print(f"      流动性: ${opp['liquidity']:,.0f}  |  24h: ${opp['volume_24h']:,.0f}")
                    print()

                # 发送通知（如果需要）
                if opportunities[0]['arbitrage'] > 2.0:
                    print("  [!!!] 高套利机会！建议立即查看！")
            else:
                print(f"  未发现套利机会（阈值: {min_arbitrage_threshold}%）")

                # 显示最佳市场
                if markets:
                    best_liquidity = max(markets, key=lambda m: float(m.get('liquidity', 0) or 0))
                    prices_str = best_liquidity.get('outcomePrices', '[]')
                    if isinstance(prices_str, str):
                        prices = json.loads(prices_str)
                        if len(prices) >= 2:
                            yes_p = float(prices[0])
                            no_p = float(prices[1])
                            combined = yes_p + no_p
                            arb = (1.0 - combined) * 100
                            print(f"\n  最活跃市场:")
                            print(f"    {best_liquidity.get('question', 'N/A')[:70]}")
                            print(f"    Yes: {yes_p*100:.2f}c  No: {no_p*100:.2f}c  套利: {arb:.2f}%")

        except requests.exceptions.ConnectionError:
            print("  [错误] 网络连接失败")
        except Exception as e:
            print(f"  [错误] {type(e).__name__}: {e}")

        # 等待下一次扫描
        print("\n  等待 30 秒后重新扫描...")
        time.sleep(30)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n监控已停止")
