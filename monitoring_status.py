"""
实时监控状态检查
显示当前监控的市场数量和状态
"""

import os
import sys
from pathlib import Path

# 设置 API Key
os.environ['PREDICT_API_KEY'] = "1b0c25d4-8ca6-4aa8-8910-cd72b311e4f6"

sys.path.insert(0, str(Path(__file__).parent))

print("=" * 70)
print(" " * 15 + "REAL-TIME MONITORING STATUS")
print("=" * 70)
print()

# 1. Polymarket 市场统计
print("[1/4] Polymarket Markets")
print("-" * 70)
from src.polymarket_api import create_polymarket_client
poly_config = {'polymarket': {'cache_seconds': 60}}
poly_client = create_polymarket_client(poly_config, use_real=True)

all_markets = poly_client.get_all_markets(limit=1000)
active_markets = [m for m in all_markets if m.get('active', True)]

print(f"  Total Markets: {len(all_markets)}")
print(f"  Active Markets: {len(active_markets)}")
print(f"  Monitoring: {len(active_markets)} markets")

# 显示热门市场
print()
print("  Top Markets by Volume:")
popular = sorted(all_markets, key=lambda m: float(m.get('volume', 0)), reverse=True)[:5]
for i, m in enumerate(popular, 1):
    vol = float(m.get('volume', 0))
    question = m.get('question', 'N/A')[:50]
    print(f"    {i}. ${vol:,.0f} - {question}...")

# 2. Predict.fun 市场统计
print()
print("[2/4] Predict.fun Markets")
print("-" * 70)
from src.api_client import PredictAPIClient
config = {'api': {'api_key': os.environ['PREDICT_API_KEY'], 'base_url': 'https://api.predict.fun'}}
predict_client = PredictAPIClient(config)

predict_markets = predict_client.get_markets(active_only=True, limit=1000)
print(f"  Total Markets: {len(predict_markets)}")
print(f"  Monitoring: {len(predict_markets)} markets")

if predict_markets:
    print()
    print("  Sample Markets:")
    for i, m in enumerate(predict_markets[:5], 1):
        question = m.get('question', m.get('title', 'N/A'))[:50]
        mid = m.get('id', m.get('slug', 'N/A'))
        print(f"    {i}. [{mid}] {question}...")

# 3. 市场匹配统计
print()
print("[3/4] Market Matching")
print("-" * 70)
from src.market_matcher import create_market_matcher
from src.probable_api import create_probable_client

match_config = {
    'market_match': {
        'min_confidence': 0.3,
        'max_matches': 10,
        'cache_minutes': 30
    }
}
matcher = create_market_matcher(match_config)

probable_client = create_probable_client(match_config, use_mock=True)

market_map = matcher.build_market_map(poly_client, predict_client, probable_client)
stats = matcher.get_statistics()

print(f"  Total Matches: {stats.get('total', 0)}")
print(f"  With Predict.fun: {stats.get('with_predict', 0)}")
print(f"  With Probable: {stats.get('with_probable', 0)}")
print(f"  Avg Confidence: {stats.get('avg_confidence', 0):.2f}")

# 4. 预期套利机会
print()
print("[4/4] Arbitrage Opportunities")
print("-" * 70)
from src.arbitrage_monitor import ArbitrageMonitor

arb_config = {
    'arbitrage': {
        'min_arbitrage_threshold': 2.0,
        'scan_interval': 10
    },
    'notification': {
        'telegram': {'enabled': False}
    },
    'logging': {'level': 'WARNING'}
}
monitor = ArbitrageMonitor(arb_config)

opportunities = monitor.scan_all_markets(poly_client, predict_client, probable_client)
arb_stats = monitor.get_statistics()

print(f"  Scan Count: {arb_stats['total_scans']}")
print(f"  Opportunities Found: {arb_stats['opportunities_found']}")
print(f"  Min Threshold: {arb_stats['min_arbitrage_threshold']}%")

if opportunities:
    print()
    print("  Current Opportunities:")
    for opp in opportunities[:3]:
        print(f"    - {opp.market_name[:40]}...")
        print(f"      Spread: {opp.arbitrage_percent:.2f}%")
        print(f"      Type: {opp.arbitrage_type.value}")

# 总结
print()
print("=" * 70)
print(" " * 20 + "MONITORING SUMMARY")
print("=" * 70)
print()
print(f"  Platform          Markets    Status")
print(f"  ─────────────────────────────────────────")
print(f"  Polymarket       {len(active_markets):>4}      Active")
print(f"  Predict.fun      {len(predict_markets):>4}      Active")
print(f"  Probable         (mock)    Active")
print()
print(f"  Total Potential Matches: {len(active_markets) * len(predict_markets):,}")
print()
print(f"  Current Monitoring: {stats['total']} matched pairs")
print(f"  Arbitrage Threshold: {arb_stats['min_arbitrage_threshold']}%")
print()
print("=" * 70)
print()
print("Next Scan: Running continuously on Railway")
print("Telegram: Notifications enabled for >2% spreads")
print()
