"""
Cross-platform prediction market arbitrage monitor (修改版）
Platforms: Polymarket, Opinion.trade, Predict.fun

使用统一市场匹配模块 src/market_matcher.py
"""

import os
import sys
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import json
import time
import traceback

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 导入统一匹配器
from src.market_matcher import MarketMatcher, create_market_matcher

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

# 配置
CONFIG = {
    'arbitrage': {
        'min_threshold': 2.0,  # 套利阈值
        'scan_interval': 30,      # 扫描间隔（秒）
        'cooldown_minutes': 10,    # 通知冷却时间（分钟）
    },
    'telegram': {
        'enabled': True,
        'bot_token': os.getenv('TELEGRAM_BOT_TOKEN', ''),
        'chat_id': os.getenv('TELEGRAM_CHAT_ID', '')
    }
}

def load_config():
    """加载配置（从环境变量或 config.yaml）"""
    config = {
        'arbitrage': {
            'min_threshold': float(os.getenv('MIN_ARBITRAGE_THRESHOLD', 2.0)),
            'scan_interval': int(os.getenv('SCAN_INTERVAL', 30)),
            'cooldown_minutes': int(os.getenv('COOLDOWN_MINUTES', 10))
        },
        'notification': {
            'telegram': {
                'enabled': os.getenv('TELEGRAM_BOT_TOKEN', '') != '',
                'chat_id': os.getenv('TELEGRAM_CHAT_ID', '') != ''
            }
        }
    }
    return config

def format_arbitrage_message(opp, scan_count: int) -> str:
    """格式化套利机会为 Telegram 消息"""
    return (
        f"<b>🎯 套利机会 #{scan_count}</b>\n"
        f"<b>市场:</b> {opp['market']}\n"
        f"<b>平台:</b> {opp['platforms']}\n"
        f"<b>方向:</b> {opp['direction']}\n"
        f"<b>套利空间:</b> {opp['arbitrage']:.2f}%\n\n"
        f"<b>Platform A:</b> Yes {opp['a_yes']}c No {opp['a_no']}c\n"
        f"<b>Platform B:</b> Yes {opp['b_yes']}c No {opp['b_no']}c\n"
        f"<b>置信度:</b> {opp['confidence']:.0%}\n"
    )

def send_arbitrage_notification(opportunities: List[Dict], scan_count: int):
    """发送套利机会到 Telegram"""
    if not opportunities:
        return

    config = load_config()
    if not config['notification']['telegram']['enabled']:
        logger.info(f"发送 {len(opportunities)} 个套利机会到 Telegram")
        # TODO: 实现 Telegram 发送逻辑
        # 这里暂时只记录日志

    return opportunities

def scan_for_arbitrage():
    """扫描跨平台套利机会（使用统一匹配模块）"""
    config = load_config()
    logger.info("开始跨平台套利扫描...")

    # 获取各平台市场数据
    # TODO: 从各 API 客户端获取数据
    markets_data = {}

    # 模拟数据（用于测试）
    markets_data['polymarket'] = [
        {'id': 'test-1', 'title': 'Test Market 1', 'yes': 0.45, 'no': 0.55, 'url': 'https://polymarket.com/event/test1'}
    ]

    try:
        # 使用统一匹配器
        matcher = create_market_matcher(config)

        # 扫描套利机会
        opportunities = []
        checked_pairs = 0
        skipped_similarity = 0
        skipped_end_date = 0

        # 遍历所有平台对
        for platform_a, platform_b in [('polymarket', 'opinion'), ('polymarket', 'predict'), ('opinion', 'predict')]:
            name_a, name_b = platform_a, platform_b
            markets_a = markets_data.get(platform_a, [])
            markets_b = markets_data.get(platform_b, [])

            if not markets_a or not markets_b:
                logger.warning(f"平台数据为空: {platform_a} 和 {platform_b}")
                continue

            logger.info(f"匹配平台: {name_a} vs {name_b}")

            # 调用统一匹配器
            matched_pairs = matcher.match_markets_cross_platform(
                markets_a, markets_b,
                title_field_a='title', title_field_b='title',
                id_field_a='id', id_field_b='id',
                platform_a=platform_a.lower(), platform_b=platform_b.lower(),
                min_similarity=config['arbitrage']['min_threshold']
            )

            logger.info(f"找到 {len(matched_pairs)} 对匹配")

            for ma, mb, confidence in matched_pairs:
                checked_pairs += 1

                # Direction 1: A Yes + B No
                combined1 = ma.get('yes', 0) + mb.get('no', 0)
                arb1 = (1.0 - combined1) * 100

                # Direction 2: B Yes + A No
                combined2 = mb.get('yes', 0) + ma.get('no', 0)
                arb2 = (1.0 - combined2) * 100

                market_key = f"{platform_a}-{platform_b}-{ma.get('id','')}-{mb.get('id','')}"

                if arb1 >= config['arbitrage']['min_threshold']:
                    opportunities.append({
                        'market': ma.get('title', ''),
                        'platforms': f"{platform_a} <-> {platform_b}",
                        'direction': f"{platform_a} Buy Yes + {platform_b} Buy No",
                        'a_yes': round(ma.get('yes', 0) * 100, 2),
                        'a_no': round(ma.get('no', 0) * 100, 2),
                        'b_yes': round(mb.get('yes', 0) * 100, 2),
                        'b_no': round(mb.get('no', 0) * 100, 2),
                        'combined': round(combined1 * 100, 2),
                        'arbitrage': round(arb1, 2),
                        'confidence': round(confidence, 2),
                        'timestamp': datetime.now().strftime('%H:%M:%S'),
                        'market_key': market_key,
                    })

                if arb2 >= config['arbitrage']['min_threshold']:
                    opportunities.append({
                        'market': mb.get('title', ''),
                        'platforms': f"{platform_a} <-> {platform_b}",
                        'direction': f"{platform_b} Buy Yes + {platform_a} Buy No",
                        'a_yes': round(mb.get('yes', 0) * 100, 2),
                        'a_no': round(ma.get('no', 0) * 100, 2),
                        'b_yes': round(mb.get('yes', 0) * 100, 2),
                        'b_no': round(mb.get('no', 0) * 100, 2),
                        'combined': round(combined2 * 100, 2),
                        'arbitrage': round(arb2, 2),
                        'confidence': round(confidence, 2),
                        'timestamp': datetime.now().strftime('%H:%M:%S'),
                        'market_key': market_key,
                    })

        logger.info(f"扫描完成: 检查了 {checked_pairs} 对，跳过 {skipped_similarity} 相似度，跳过 {skipped_end_date} 日期不匹配，找到 {len(opportunities)} 个套利机会")

        # 发送通知
        send_arbitrage_notification(opportunities, checked_pairs)

        return opportunities

    except Exception as e:
        logger.error(f"扫描出错: {e}")
        logger.error(traceback.format_exc())
        return []


def main():
    """主函数"""
    logger.info("Continuous Monitor 启动...")
    logger.info(f"项目根目录: {project_root}")
    logger.info(f"Python 路径: {sys.executable}")

    # 加载配置
    config = load_config()

    # 测试统一匹配器
    try:
        matcher = create_market_matcher(config)
        logger.info("统一匹配器创建成功")
    except Exception as e:
        logger.error(f"统一匹配器创建失败: {e}")
        logger.error(traceback.format_exc())

    # 开始扫描
    logger.info("开始套利扫描...")
    opportunities = scan_for_arbitrage()

    logger.info(f"找到 {len(opportunities)} 个套利机会")

    # 持续运行
    while True:
        time.sleep(config['arbitrage']['scan_interval'])
        opportunities = scan_for_arbitrage()

        # 记录并发送通知
        for opp in opportunities[:5]:  # 每次只发送前 5 个
            send_arbitrage_notification([opp], 0)

    time.sleep(60)  # 每分钟检查一次 Railway 健康状态

if __name__ == '__main__':
    main()
