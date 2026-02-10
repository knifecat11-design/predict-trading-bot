"""
Railway 部署启动脚本
当前监控: Opinion.trade ↔ Polymarket 跨平台套利
"""
import sys
import os

# 确保项目根目录在 Python 路径中
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

if __name__ == '__main__':
    import continuous_monitor
    sys.exit(continuous_monitor.main())
