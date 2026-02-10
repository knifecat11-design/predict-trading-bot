"""
Railway 环境测试脚本
"""
import sys
import os

print("=" * 60)
print("  Railway 环境诊断")
print("=" * 60)

# 1. 当前工作目录
print(f"\n[1] 当前目录:")
print(f"  cwd: {os.getcwd()}")

# 2. 文件列表
print(f"\n[2] 项目文件:")
print(f"  start_arbitrage.py: {os.path.exists('start_arbitrage.py')}")
print(f"  continuous_monitor.py: {os.path.exists('continuous_monitor.py')}")
print(f"  src/ 目录: {os.path.exists('src')}")
print(f"  src/opinion_api.py: {os.path.exists('src/opinion_api.py')}")

# 3. Python 路径
print(f"\n[3] Python 路径:")
for i, p in enumerate(sys.path[:5]):
    print(f"  {i}: {p}")

# 4. 尝试导入
print(f"\n[4] 模块导入测试:")
try:
    sys.path.insert(0, os.getcwd())
    import continuous_monitor
    print("  continuous_monitor: OK")
except Exception as e:
    print(f"  continuous_monitor: FAILED - {e}")

try:
    from src import opinion_api
    print("  src.opinion_api: OK")
except Exception as e:
    print(f"  src.opinion_api: FAILED - {e}")

print("\n" + "=" * 60)
