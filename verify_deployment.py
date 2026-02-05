"""
部署验证脚本
在推送到 Railway 之前验证代码是否可以正常运行
"""

import sys
import os
from pathlib import Path

# 设置环境变量模拟 Railway
os.environ['USE_HYBRID_MODE'] = 'true'
os.environ['RAILWAY_SERVICE_NAME'] = 'arbitrage-monitor'
os.environ['TELEGRAM_BOT_TOKEN'] = 'test_token'
os.environ['TELEGRAM_CHAT_ID'] = 'test_chat_id'

sys.path.insert(0, str(Path(__file__).parent))


def test_imports():
    """测试所有模块导入"""
    print("[TEST 1/5] Testing imports...")
    try:
        from src.market_matcher import create_market_matcher
        from src.polymarket_api import create_polymarket_client
        from src.api_client import create_api_client
        from src.probable_api import create_probable_client
        from src.arbitrage_monitor import ArbitrageMonitor
        print("  [OK] All modules imported")
        return True
    except Exception as e:
        print(f"  [FAIL] Import error: {e}")
        return False


def test_config():
    """测试配置加载"""
    print("[TEST 2/5] Testing config loading...")
    try:
        from arbitrage_main import load_config
        config = load_config()
        assert config['arbitrage']['min_arbitrage_threshold'] >= 0
        print(f"  [OK] Config loaded (threshold: {config['arbitrage']['min_arbitrage_threshold']}%)")
        return True
    except Exception as e:
        print(f"  [FAIL] Config error: {e}")
        return False


def test_clients():
    """测试客户端创建"""
    print("[TEST 3/5] Testing client creation...")
    try:
        from arbitrage_main import load_config
        from src.polymarket_api import create_polymarket_client
        from src.api_client import create_api_client
        from src.probable_api import create_probable_client

        config = load_config()

        # Real Polymarket client (will use HTTP API if CLOB unavailable)
        poly_client = create_polymarket_client(config, use_real=True)
        print("  [OK] Polymarket client created")

        # Mock clients
        predict_client = create_api_client(config, use_mock=True)
        probable_client = create_probable_client(config, use_mock=True)
        print("  [OK] Predict.fun mock client created")
        print("  [OK] Probable.markets mock client created")
        return True
    except Exception as e:
        print(f"  [FAIL] Client creation error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_matcher():
    """测试市场匹配器"""
    print("[TEST 4/5] Testing market matcher...")
    try:
        from arbitrage_main import load_config
        from src.market_matcher import create_market_matcher
        from src.polymarket_api import create_polymarket_client
        from src.api_client import create_api_client
        from src.probable_api import create_probable_client

        config = load_config()
        matcher = create_market_matcher(config)

        poly_client = create_polymarket_client(config, use_real=True)
        predict_client = create_api_client(config, use_mock=True)
        probable_client = create_probable_client(config, use_mock=True)

        market_map = matcher.build_market_map(poly_client, predict_client, probable_client)
        print(f"  [OK] Matcher created ({len(market_map)} markets matched)")
        return True
    except Exception as e:
        print(f"  [FAIL] Matcher error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_monitor():
    """测试套利监控器"""
    print("[TEST 5/5] Testing arbitrage monitor...")
    try:
        from arbitrage_main import load_config
        from src.arbitrage_monitor import ArbitrageMonitor
        from src.polymarket_api import create_polymarket_client
        from src.api_client import create_api_client
        from src.probable_api import create_probable_client

        config = load_config()
        monitor = ArbitrageMonitor(config)
        print("  [OK] ArbitrageMonitor initialized")

        poly_client = create_polymarket_client(config, use_real=True)
        predict_client = create_api_client(config, use_mock=True)
        probable_client = create_probable_client(config, use_mock=True)

        opportunities = monitor.scan_all_markets(poly_client, predict_client, probable_client)
        print(f"  [OK] Scan completed ({len(opportunities)} opportunities)")

        stats = monitor.get_statistics()
        print(f"  [OK] Statistics: {stats['total_scans']} scans, {stats['opportunities_found']} opportunities")
        return True
    except Exception as e:
        print(f"  [FAIL] Monitor error: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """运行所有测试"""
    print()
    print("=" * 60)
    print("  DEPLOYMENT VERIFICATION TEST")
    print("=" * 60)
    print()

    results = []
    results.append(test_imports())
    results.append(test_config())
    results.append(test_clients())
    results.append(test_matcher())
    results.append(test_monitor())

    print()
    print("=" * 60)

    if all(results):
        print("  [SUCCESS] All tests passed!")
        print("=" * 60)
        print()
        print("Ready for Railway deployment.")
        print()
        print("Next steps:")
        print("  1. Push to GitHub (if not already done)")
        print("  2. Monitor Railway deployment")
        print("  3. Check logs for errors")
        print("  4. Verify Telegram notification is received")
        return 0
    else:
        print("  [FAILURE] Some tests failed!")
        print("=" * 60)
        print()
        print("Please fix the errors above before deploying.")
        return 1


if __name__ == '__main__':
    sys.exit(main())
