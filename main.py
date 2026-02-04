"""
Predict.fun 自动化交易脚本 - 主程序
"""

import os
import sys
import logging
import signal
from pathlib import Path

import yaml


def setup_logging(config: dict):
    """配置日志系统"""
    log_level = config.get('logging', {}).get('level', 'INFO')
    log_file = config.get('logging', {}).get('file', 'logs/trading.log')

    # 确保日志目录存在
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    # 配置日志格式
    log_format = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'

    # 配置根日志器
    logging.basicConfig(
        level=getattr(logging, log_level),
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )

    return logging.getLogger(__name__)


def load_config(config_path: str = 'config.yaml') -> dict:
    """加载配置文件"""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    return config


def main():
    """主函数"""
    print("=" * 60)
    print("Predict.fun 自动化交易脚本")
    print("=" * 60)
    print()

    # 加载配置
    try:
        config = load_config()
    except FileNotFoundError as e:
        print(f"错误: {e}")
        print("请确保 config.yaml 文件存在")
        return 1

    # 设置日志
    logger = setup_logging(config)
    logger.info("程序启动")

    # 导入模块
    try:
        from src.api_client import create_api_client
        from src.strategy import Strategy, PositionManager
        from src.risk_manager import RiskManager
        from src.order_manager import OrderManager
    except ImportError as e:
        logger.error(f"导入模块失败: {e}")
        print("请确保已安装所有依赖: pip install -r requirements.txt")
        return 1

    # 创建组件
    logger.info("创建 API 客户端 (模拟模式)")
    api_client = create_api_client(config, use_mock=True)

    logger.info("初始化策略模块")
    strategy = Strategy(config)

    logger.info("初始化仓位管理器")
    position_manager = PositionManager(config)

    logger.info("初始化风险管理器")
    risk_manager = RiskManager(config)

    logger.info("初始化订单管理器")
    order_manager = OrderManager(
        api_client, strategy, position_manager, risk_manager, config
    )

    # 设置信号处理
    def signal_handler(sig, frame):
        logger.info("收到中断信号")
        order_manager.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # 显示启动信息
    print()
    print("配置信息:")
    print(f"  市场 ID: {config['market']['market_id']}")
    print(f"  基础仓位: {config['market']['base_position_size']}")
    print(f"  挂单范围: ±{config['strategy']['spread_percent']}%")
    print(f"  撤单阈值: {config['strategy']['cancel_threshold']}%")
    print(f"  最大敞口: {config['market']['max_exposure']}")
    print()
    print("运行模式: 模拟模式 (使用模拟数据)")
    print("按 Ctrl+C 停止程序")
    print()
    print("-" * 60)
    print()

    # 启动交易循环
    try:
        order_manager.start()
    except Exception as e:
        logger.error(f"主循环错误: {e}", exc_info=True)
        return 1

    logger.info("程序正常退出")
    return 0


if __name__ == '__main__':
    sys.exit(main())
