"""
套利监控主程序
监控 Polymarket 和 Predict.fun 之间的套利机会，通过 Telegram 推送通知
策略：Yes价格 + No价格 < 100% 时存在套利空间
"""

import os
import sys
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta

# Railway 环境兼容性处理
try:
    import signal
    HAS_SIGNAL = True
except ImportError:
    HAS_SIGNAL = False

import yaml


def setup_logging(config: dict):
    """配置日志系统"""
    log_level = config.get('logging', {}).get('level', 'INFO')
    log_file = config.get('logging', {}).get('file', 'logs/trading.log')

    # Railway 环境下，日志输出到标准输出
    log_format = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'

    handlers = [logging.StreamHandler(sys.stdout)]

    # 只在本地环境写入文件
    if not os.getenv('RAILWAY_ENVIRONMENT'):
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(log_file, encoding='utf-8'))
        except:
            pass

    logging.basicConfig(
        level=getattr(logging, log_level),
        format=log_format,
        datefmt=date_format,
        handlers=handlers
    )

    return logging.getLogger(__name__)


def load_config(config_path: str = 'config.yaml') -> dict:
    """加载配置文件"""
    # 优先使用 config_helper，支持环境变量
    try:
        from src.config_helper import load_config as load_env_config
        return load_env_config(config_path)
    except ImportError:
        # 回退到原始方法
        if not os.path.exists(config_path):
            # Railway 环境下可能没有 config.yaml
            config = {
                'arbitrage': {
                    'enabled': True,
                    'min_arbitrage_threshold': float(os.getenv('MIN_ARBITRAGE_THRESHOLD', 2.0)),
                    'scan_interval': int(os.getenv('SCAN_INTERVAL', 10)),
                    'cooldown_minutes': int(os.getenv('COOLDOWN_MINUTES', 5))
                },
                'notification': {
                    'telegram': {
                        'enabled': True,
                        'bot_token': os.getenv('TELEGRAM_BOT_TOKEN', ''),
                        'chat_id': os.getenv('TELEGRAM_CHAT_ID', '')
                    }
                },
                'logging': {
                    'level': os.getenv('LOG_LEVEL', 'INFO')
                }
            }
            return config

        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        return config


def print_banner():
    """打印启动横幅"""
    print()
    print("=" * 60)
    print("  Polymarket ↔ Predict.fun 套利监控系统")
    print("  策略: Yes + No < 100% 时套利")
    print("=" * 60)
    print()


def print_startup_info(config: dict):
    """打印启动信息"""
    arb_config = config.get('arbitrage', {})
    notification_config = config.get('notification', {})

    print("配置信息:")
    print(f"  最小套利空间: {arb_config.get('min_arbitrage_threshold', 2.0)}%")
    print(f"  扫描间隔: {arb_config.get('scan_interval', 10)} 秒")
    print()

    telegram_enabled = notification_config.get('telegram', {}).get('enabled', False)
    print(f"  Telegram通知: {'✓ 启用' if telegram_enabled else '✗ 未启用'}")

    print()
    print("运行模式: 模拟模式 (使用模拟数据)")
    print("-" * 60)
    print()


def main():
    """主函数"""
    print_banner()

    # 加载配置
    try:
        config = load_config()
    except Exception as e:
        print(f"错误: {e}")
        return 1

    # 设置日志
    logger = setup_logging(config)
    logger.info("=" * 50)
    logger.info("套利监控系统启动")
    logger.info("=" * 50)

    # 打印启动信息
    print_startup_info(config)

    # 导入模块
    try:
        from src.api_client import create_api_client
        from src.polymarket_api import create_polymarket_client
        from src.arbitrage_monitor import ArbitrageMonitor
        from src.notifier import TelegramNotifier
    except ImportError as e:
        logger.error(f"导入模块失败: {e}")
        return 1

    # 创建组件
    logger.info("创建 API 客户端...")
    predict_client = create_api_client(config, use_mock=True)
    polymarket_client = create_polymarket_client(config, use_mock=True)

    logger.info("初始化套利监控器...")
    monitor = ArbitrageMonitor(config)

    logger.info("初始化 Telegram 通知器...")
    notifier = TelegramNotifier(config)

    # 发送启动通知
    if config.get('notification', {}).get('telegram', {}).get('enabled', False):
        logger.info("发送启动通知...")
        try:
            notifier.send_test_message()
        except Exception as e:
            logger.warning(f"发送测试消息失败: {e}")

    # 运行状态
    running = True
    scan_count = 0

    # 上次通知时间（用于冷却）
    last_notification_time = {}
    cooldown_minutes = config.get('arbitrage', {}).get('cooldown_minutes', 5)
    scan_interval = config.get('arbitrage', {}).get('scan_interval', 10)

    # 信号处理（只在非 Railway 环境）
    if HAS_SIGNAL and not os.getenv('RAILWAY_ENVIRONMENT'):
        def signal_handler(sig, frame):
            nonlocal running
            logger.info("收到停止信号...")
            running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    # 主循环
    logger.info("开始监控套利机会...")

    try:
        while running:
            scan_count += 1

            try:
                # 扫描套利机会
                opportunities = monitor.scan_all_markets(
                    polymarket_client, predict_client
                )

                if opportunities:
                    logger.info(f"发现 {len(opportunities)} 个套利机会")

                    for opp in opportunities:
                        market_key = f"{opp.market_name}:{opp.arbitrage_type.value}"

                        # 检查冷却时间
                        now = datetime.now()
                        if market_key in last_notification_time:
                            last_time = last_notification_time[market_key]
                            if now - last_time < timedelta(minutes=cooldown_minutes):
                                logger.debug(f"市场 {market_key} 在冷却期内，跳过通知")
                                continue

                        # 发送通知
                        logger.info(f"发送套利通知: {opp.market_name}")
                        try:
                            notifier.send_arbitrage_alert(opp)
                        except Exception as e:
                            logger.error(f"发送通知失败: {e}")
                        last_notification_time[market_key] = now

                # 定期输出统计
                if scan_count % 6 == 0:
                    stats = monitor.get_statistics()
                    logger.info(f"扫描统计: 总扫描 {stats['total_scans']} 次, "
                              f"发现机会 {stats['opportunities_found']} 次")

            except Exception as e:
                logger.error(f"扫描过程出错: {e}", exc_info=True)

            # 等待下一次扫描
            time.sleep(scan_interval)

    except KeyboardInterrupt:
        logger.info("收到键盘中断")
    finally:
        # 输出最终统计
        stats = monitor.get_statistics()
        logger.info("=" * 50)
        logger.info("监控停止")
        logger.info(f"总扫描次数: {stats['total_scans']}")
        logger.info(f"发现机会数: {stats['opportunities_found']}")
        logger.info("=" * 50)

    print()
    print("监控已停止")
    return 0


if __name__ == '__main__':
    sys.exit(main())
