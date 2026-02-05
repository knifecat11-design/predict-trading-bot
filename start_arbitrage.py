#!/usr/bin/env python3
"""
Railway 启动包装器
提供额外的错误处理和监控
"""
import sys
import logging
import traceback
from datetime import datetime

# 配置基础日志（在主程序日志配置之前）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] STARTUP: %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


def main():
    """主入口函数"""
    logger.info("=" * 60)
    logger.info("套利监控系统启动包装器")
    logger.info(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    try:
        # 导入并运行主程序
        from arbitrage_main import main as arbitrage_main
        return arbitrage_main()

    except KeyboardInterrupt:
        logger.info("收到键盘中断信号")
        return 0

    except SystemExit as e:
        # SystemExit 是正常的退出（sys.exit）
        logger.info(f"系统退出，代码: {e.code}")
        return e.code if e.code is not None else 0

    except Exception as e:
        # 捕获所有未处理的异常
        logger.error("=" * 60)
        logger.error("未捕获的异常，程序即将退出")
        logger.error("=" * 60)
        logger.error(f"异常类型: {type(e).__name__}")
        logger.error(f"异常信息: {e}")
        logger.error("=" * 60)
        logger.error("完整堆栈跟踪:")
        logger.error(traceback.format_exc())
        logger.error("=" * 60)
        return 1


if __name__ == '__main__':
    exit_code = main()
    logger.info(f"程序退出，返回码: {exit_code}")
    sys.exit(exit_code)
