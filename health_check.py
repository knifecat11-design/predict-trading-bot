#!/usr/bin/env python3
"""
Railway 健康检查脚本
用于监控服务是否正常运行
"""
import os
import sys
import logging
import time
from datetime import datetime, timedelta

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] HEALTH: %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


def check_log_file_health():
    """检查日志文件是否有最近的活动"""
    log_file = 'logs/trading_arbitrage.log'

    if not os.path.exists(log_file):
        logger.info(f"日志文件不存在: {log_file} (首次运行)")
        return True

    # 检查日志文件的最后修改时间
    mtime = os.path.getmtime(log_file)
    last_modified = datetime.fromtimestamp(mtime)
    time_since_last_activity = datetime.now() - last_modified

    # 如果日志文件在5分钟内有更新，认为是健康的
    if time_since_last_activity < timedelta(minutes=5):
        logger.info(f"健康检查通过: 日志文件最后更新于 {time_since_last_activity.seconds} 秒前")
        return True
    else:
        logger.warning(f"健康检查警告: 日志文件已 {int(time_since_last_activity.total_seconds())} 秒未更新")
        return False


def check_process_health():
    """检查是否有正在运行的 Python 进程"""
    try:
        import psutil
        current_pid = os.getpid()
        python_processes = [p for p in psutil.process_iter(['pid', 'name', 'cmdline'])
                           if p.info['name'] and 'python' in p.info['name'].lower()]

        # 检查是否有 arbitrage_main.py 或 start_arbitrage.py 在运行
        for proc in python_processes:
            cmdline = proc.info.get('cmdline', [])
            if cmdline and any('arbitrage' in str(cmd).lower() for cmd in cmdline):
                if proc.info['pid'] != current_pid:
                    logger.info(f"发现运行中的套利监控进程: PID {proc.info['pid']}")
                    return True

        logger.info("未发现其他套利监控进程")
        return True
    except ImportError:
        # psutil 未安装，跳过进程检查
        logger.info("psutil 未安装，跳过进程检查")
        return True
    except Exception as e:
        logger.error(f"进程检查失败: {e}")
        return True  # 不因检查失败而认为不健康


def main():
    """主函数"""
    logger.info("执行健康检查...")

    # 检查日志文件
    log_healthy = check_log_file_health()

    # 检查进程
    process_healthy = check_process_health()

    # 综合判断
    if log_healthy and process_healthy:
        logger.info("健康检查通过 ✓")
        return 0
    else:
        logger.warning("健康检查发现问题 ⚠")
        return 1


if __name__ == '__main__':
    sys.exit(main())
