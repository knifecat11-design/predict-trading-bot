"""
Railway 部署启动脚本
混合模式：Polymarket 真实数据 + Predict.fun 模拟数据
"""

import os
import sys

# 配置环境变量（Railway 部署）
# 混合模式：Polymarket（真实）+ Predict.fun（模拟）
os.environ.setdefault('USE_HYBRID_MODE', 'true')
os.environ.setdefault('USE_REAL_API', 'false')

if __name__ == '__main__':
    # 导入并运行主程序
    import arbitrage_main
    sys.exit(arbitrage_main.main())
