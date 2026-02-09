"""
Railway 部署启动脚本
当前监控: Opinion.trade ↔ Polymarket 跨平台套利
"""

import sys

if __name__ == '__main__':
    import continuous_monitor
    sys.exit(continuous_monitor.main())
