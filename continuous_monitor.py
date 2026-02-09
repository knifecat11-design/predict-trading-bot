"""
持续套利监控 - Opinion.trade ↔ Polymarket
通过 Telegram 发送套利机会通知
"""

import os
import sys
import time
import logging
from datetime import datetime, timedelta

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
    """加载配置（支持环境变量覆盖）"""
    try:
        from src.config_helper import load_config as load_env_config
        return load_env_config()
    except ImportError:
        # 回退：直接读 config.yaml
        import yaml
        config_path = 'config.yaml'
        if not os.path.exists(config_path):
            return {
                'arbitrage': {
                    'min_arbitrage_threshold': float(os.getenv('MIN_ARBITRAGE_THRESHOLD', 2.0)),
                    'scan_interval': int(os.getenv('SCAN_INTERVAL', 30)),
                    'cooldown_minutes': int(os.getenv('COOLDOWN_MINUTES', 10))
                },
                'opinion': {
                    'api_key': os.getenv('OPINION_API_KEY', ''),
                    'base_url': 'https://proxy.opinion.trade:8443/openapi',
                },
                'opinion_poly': {
                    'min_arbitrage_threshold': float(os.getenv('OPINION_POLY_THRESHOLD', 2.0)),
                    'min_confidence': 0.2,
                },
                'notification': {
                    'telegram': {
                        'enabled': True,
                        'bot_token': os.getenv('TELEGRAM_BOT_TOKEN', ''),
                        'chat_id': os.getenv('TELEGRAM_CHAT_ID', '')
                    }
                },
                'logging': {'level': os.getenv('LOG_LEVEL', 'INFO')}
            }
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

# Telegram 限流状态
_telegram_rate_limited_until = 0

def send_telegram_notification(message, config):
    """发送 Telegram 通知（带限流处理）"""
    global _telegram_rate_limited_until

    try:
        # 检查是否还在限流期
        if time.time() < _telegram_rate_limited_until:
            remaining = int(_telegram_rate_limited_until - time.time())
            logging.debug(f"Telegram 限流中，剩余 {remaining} 秒")
            return False

        token = config.get('notification', {}).get('telegram', {}).get('bot_token')
        chat_id = config.get('notification', {}).get('telegram', {}).get('chat_id')

        if not token or not chat_id:
            logging.warning("Telegram bot_token 或 chat_id 未配置")
            return False

        import requests
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'HTML'
        }

        response = requests.post(url, json=data, timeout=10)

        if response.status_code == 200:
            return True
        elif response.status_code == 429:
            # 限流处理
            retry_after = response.json().get('parameters', {}).get('retry_after', 60)
            _telegram_rate_limited_until = time.time() + retry_after
            logging.warning(f"Telegram 限流，{retry_after} 秒后重试")
            return False
        else:
            logging.error(f"Telegram 发送失败: {response.status_code} {response.text[:200]}")
            return False

    except Exception as e:
        logging.error(f"发送 Telegram 通知失败: {e}")
        return False

def scan_opinion_poly(logger, config):
    """扫描 Opinion ↔ Polymarket 套利"""
    try:
        from src.polymarket_api import RealPolymarketClient
        from src.opinion_api import create_opinion_client
        from src.opinion_poly_monitor import create_opinion_poly_monitor

        poly_client = RealPolymarketClient(config)
        opinion_client = create_opinion_client(config, use_mock=False)
        monitor = create_opinion_poly_monitor(config)

        opportunities = monitor.scan_opinion_poly_arbitrage(poly_client, opinion_client)

        return opportunities, "Opinion <-> Polymarket"

    except Exception as e:
        logger.error(f"Opinion ↔ Polymarket 扫描失败: {e}", exc_info=True)
        return [], "Opinion <-> Polymarket"

def format_opportunity_message(opp, platform_pair, scan_count):
    """格式化套利机会通知消息"""
    from src.opinion_poly_monitor import OpinionPolyOpportunity

    header = f"<b>🎯 套利机会 #{scan_count}</b>\n"
    header += f"<b>平台:</b> {platform_pair}\n"
    header += f"<b>时间:</b> {datetime.now().strftime('%H:%M:%S')}\n"

    if isinstance(opp, OpinionPolyOpportunity):
        body = f"<b>市场:</b> {opp.market_name}\n"
        body += f"<b>策略:</b> {opp.arbitrage_type.value}\n"
        body += f"<b>套利空间:</b> {opp.arbitrage_percent:.2f}%\n"
        body += f"<b>组合价格:</b> {opp.combined_price:.2f}%\n\n"

        body += f"<b>Polymarket:</b>\n"
        body += f"  Yes: {opp.poly_yes_price:.2f}¢ No: {opp.poly_no_price:.2f}¢\n"
        body += f"  操作: {opp.poly_action}\n\n"

        body += f"<b>Opinion:</b>\n"
        body += f"  Yes: {opp.opinion_yes_price:.2f}¢ No: {opp.opinion_no_price:.2f}¢\n"
        body += f"  操作: {opp.opinion_action}\n\n"

        body += f"<b>置信度:</b> {opp.match_confidence:.2f}"
    else:
        body = str(opp)

    return f"{header}\n{body}"

def main():
    """主函数"""
    print()
    print("=" * 70)
    print("  持续套利监控系统")
    print("  平台: Opinion.trade ↔ Polymarket")
    print("  通知: Telegram")
    print("=" * 70)
    print()

    logger = setup_logging()
    config = load_config()

    # 读取配置
    arb_config = config.get('arbitrage', {})
    op_config = config.get('opinion_poly', {})
    scan_interval = arb_config.get('scan_interval', 30)
    cooldown_minutes = arb_config.get('cooldown_minutes', 10)

    # 检查关键配置
    opinion_key = config.get('opinion', {}).get('api_key', '')
    tg_token = config.get('notification', {}).get('telegram', {}).get('bot_token', '')

    logger.info(f"Opinion API Key: {'已配置' if opinion_key else '未配置'}")
    logger.info(f"Telegram 通知: {'已配置' if tg_token else '未配置'}")
    logger.info(f"扫描间隔: {scan_interval} 秒")
    logger.info(f"冷却时间: {cooldown_minutes} 分钟")
    logger.info(f"Opinion-Poly 套利阈值: {op_config.get('min_arbitrage_threshold', 2.0)}%")
    logger.info("")

    # 发送启动通知
    sent = send_telegram_notification(
        f"🚀 <b>套利监控系统启动</b>\n"
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"监控: Opinion.trade ↔ Polymarket\n"
        f"套利阈值: {op_config.get('min_arbitrage_threshold', 2.0)}%\n"
        f"Opinion API: {'已激活' if opinion_key else '未配置'}",
        config
    )
    if sent:
        logger.info("启动通知已发送")
    else:
        logger.warning("启动通知发送失败（可能限流中），监控继续运行")

    # 运行状态
    running = True
    scan_count = 0
    last_notifications = {}

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

            # 扫描 Opinion ↔ Polymarket
            opportunities, platform_name = scan_opinion_poly(logger, config)
            logger.info(f"{platform_name}: 发现 {len(opportunities)} 个机会")

            for opp in opportunities:
                # 生成唯一标识符
                market_key = f"{platform_name}:{getattr(opp, 'market_name', 'unknown')[:30]}"

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
                        logger.info(f"  ✓ 已发送通知: {getattr(opp, 'market_name', 'Unknown')[:30]}")
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
            time.sleep(scan_interval)

    except KeyboardInterrupt:
        logger.info("收到键盘中断")
    finally:
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
