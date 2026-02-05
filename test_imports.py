"""测试所有模块导入"""
import sys

print("Python version:", sys.version)
print("Testing imports...")

try:
    import yaml
    print("✓ yaml")
except Exception as e:
    print(f"✗ yaml: {e}")

try:
    import requests
    print("✓ requests")
except Exception as e:
    print(f"✗ requests: {e}")

try:
    from src.config_helper import load_config
    print("✓ config_helper")
except Exception as e:
    print(f"✗ config_helper: {e}")

try:
    from src.api_client import create_api_client
    print("✓ api_client")
except Exception as e:
    print(f"✗ api_client: {e}")

try:
    from src.polymarket_api import create_polymarket_client
    print("✓ polymarket_api")
except Exception as e:
    print(f"✗ polymarket_api: {e}")

try:
    from src.arbitrage_monitor import ArbitrageMonitor
    print("✓ arbitrage_monitor")
except Exception as e:
    print(f"✗ arbitrage_monitor: {e}")

try:
    from src.notifier import TelegramNotifier
    print("✓ notifier")
except Exception as e:
    print(f"✗ notifier: {e}")

print("\nAll imports successful!")
