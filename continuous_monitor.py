"""
持续套利监控 - 支持多平台组合
监控 Polymarket ↔ Predict.fun ↔ Kalshi
通过 Telegram 发送套利机会通知
"""

import os
import sys
import time
import logging
import yaml
from datetime import datetime, timedelta
from pathlib import Path

# UTF-8 编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

def setup_logging(level=logging.INFO):
    """配置日志"""
    handlers = [logging.StreamHandler(sys.stdout)]
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=handlers
    )
    return logging.getLogger(__name__)

def load_config():
    """加载配置"""
    config_path = 'config.yaml'
    if not os.path.exists(config_path):
        return {
            'arbitrage': {
                'min_arbitrage_threshold': 5.0,
                'scan_interval': 30,
                'cooldown_minutes': 10
            },
            'logging': {'level': 'INFO'}
        }

    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def send_telegram_notification(message, config):
    """发送 Telegram 通知"""
    try:
        token = config.get('notification', {}).get('telegram', {}).get('bot_token')
        chat_id = config.get('notification', {}).get('telegram', {}).get('chat_id')

        if not token or not chat_id:
            return False

        import requests
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'HTML'
        }

        response = requests.post(url, json=data, timeout=10)
        return response.status_code == 200

    except Exception as e:
        logging.error(f"发送 Telegram 通知失败: {e}")
        return False

def scan_kalshi_poly(logger, config):
    """扫描 Kalshi <-> Polymarket 套利"""
    try:
        from src.polymarket_api import RealPolymarketClient
        from src.kalshi_api import create_kalshi_client
        from src.cross_platform_monitor import create_cross_platform_monitor

        poly_client = RealPolymarketClient(config)
        kalshi_client = create_kalshi_client(config, use_mock=True)  # 使用模拟模式测试
        monitor = create_cross_platform_monitor(config)

        opportunities = monitor.scan_cross_platform_arbitrage(poly_client, kalshi_client)

        return opportunities, "Kalshi <-> Polymarket"

    except Exception as e:
        logger.error(f"Kalshi <-> Polymarket 扫描失败: {e}")
        return [], "Kalshi <-> Polymarket"

def scan_poly_predict(logger, config):
    """扫描 Polymarket ↔ Predict.fun 套利"""
    try:
        from src.polymarket_api import RealPolymarketClient
        from src.api_client import create_api_client
        from src.hedged_arbitrage_monitor import create_hedged_arbitrage_monitor

        poly_client = RealPolymarketClient(config)
        predict_client = create_api_client(config, use_mock=True)  # Predict.fun API 待激活
        monitor = create_hedged_arbitrage_monitor(config)

        opportunities = monitor.scan_for_hedged_arbitrage(poly_client, predict_client)

        return opportunities, "Polymarket <-> Predict.fun"

    except Exception as e:
        logger.error(f"Polymarket <-> Predict.fun 扫描失败: {e}")
        return [], "Polymarket <-> Predict.fun"

def format_opportunity_message(opp, platform_pair, scan_count):
    """格式化套利机会通知消息"""
    from src.cross_platform_monitor import format_cross_platform_opportunity
    from src.hedged_arbitrage_monitor import format_hedged_opportunity

    header = f"<b>🎯 套利机会 #{scan_count}</b>\n"
    header += f"<b>平台:</b> {platform_pair}\n"
    header += f"<b>时间:</b> {datetime.now().strftime('%H:%M:%S')}\n"

    # 根据类型格式化
    if hasattr(opp, 'strategy'):
        # HedgedArbitrageOpportunity
        body = format_hedged_opportunity(opp).replace('🎯', '').strip()
    else:
        # CrossPlatformOpportunity
        body = format_cross_platform_opportunity(opp).replace('🔄', '').strip()

    # 转换为 HTML 格式
    body = body.replace('<', '&lt;').replace('>', '&gt;')
    body = body.replace('\n', '\n')  # 保持换行

    return f"{header}\n{body}"

def main():
    """主函数"""
    print()
    print("=" * 70)
    print("  持续套利监控系统")
    print("  平台: Polymarket ↔ Predict.fun ↔ Kalshi")
    print("  通知: Telegram")
    print("=" * 70)
    print()

    logger = setup_logging()
    config = load_config()

    arb_config = config.get('arbitrage', {})
    scan_interval = arb_config.get('scan_interval', 30)
    cooldown_minutes = arb_config.get('cooldown_minutes', 10)

    logger.info(f"扫描间隔: {scan_interval} 秒")
    logger.info(f"冷却时间: {cooldown_minutes} 分钟")
    logger.info(f"最小套利阈值: {arb_config.get('min_arbitrage_threshold', 5.0)}%")
    logger.info("")

    # 发送启动通知
    send_telegram_notification(
        f"🚀 <b>套利监控系统启动</b>\n"
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"监控: Polymarket ↔ Predict.fun ↔ Kalshi\n"
        f"阈值: {arb_config.get('min_arbitrage_threshold', 5.0)}%",
        config
    )

    # 运行状态
    running = True
    scan_count = 0
    last_notifications = {}  # {market_key: timestamp}

    # 信号处理
    try:
        import signal
        def signal_handler(sig, frame):
            nonlocal running
            logger.info("收到停止信号...")
            running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    except:
        pass

    # 主循环
    logger.info("开始持续监控...")

    try:
        while running:
            scan_count += 1
            logger.info(f"=[第 {scan_count} 次扫描]=")

            all_opportunities = []

            # 扫描各个平台组合
            scanners = [
                scan_kalshi_poly,
                scan_poly_predict,
            ]

            for scanner in scanners:
                opportunities, platform_name = scanner(logger, config)
                logger.info(f"{platform_name}: 发现 {len(opportunities)} 个机会")

                for opp in opportunities:
                    # 生成唯一标识符
                    if hasattr(opp, 'market_name'):
                        market_key = f"{platform_name}:{opp.market_name[:30]}"
                    else:
                        market_key = f"{platform_name}:{scan_count}"

                    # 检查冷却时间
                    now = datetime.now()
                    if market_key in last_notifications:
                        last_time = last_notifications[market_key]
                        if now - last_time < timedelta(minutes=cooldown_minutes):
                            logger.debug(f"  {market_key} 在冷却期内，跳过")
                            continue

                    # 发送 Telegram 通知
                    try:
                        message = format_opportunity_message(opp, platform_name, scan_count)
                        if send_telegram_notification(message, config):
                            logger.info(f"  ✓ 已发送通知: {opp.market_name[:30] if hasattr(opp, 'market_name') else 'Unknown'}")
                            last_notifications[market_key] = now
                        else:
                            logger.warning(f"  ✗ Telegram 通知失败")
                    except Exception as e:
                        logger.error(f"  ✗ 发送通知失败: {e}")

                    all_opportunities.append(opp)

            # 定期输出统计
            if scan_count % 10 == 0:
                logger.info(f"[统计] 已扫描 {scan_count} 次")

            # 等待下一次扫描
            logger.info(f"等待 {scan_interval} 秒...")
            print()
            time.sleep(scan_interval)

    except KeyboardInterrupt:
        logger.info("收到键盘中断")
    finally:
        # 发送停止通知
        send_telegram_notification(
            f"⏹ <b>套利监控系统停止</b>\n"
            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"总扫描: {scan_count} 次",
            config
        )

    print()
    print("监控已停止")
    return 0

if __name__ == '__main__':
    sys.exit(main())
