"""
配置辅助模块
支持从环境变量读取配置
"""

import os
import yaml


def load_config(config_path: str = 'config.yaml') -> dict:
    """
    加载配置文件，环境变量优先

    Args:
        config_path: 配置文件路径

    Returns:
        配置字典
    """
    if not os.path.exists(config_path):
        # Railway 环境下可能没有 config.yaml
        config = {}
    else:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}

    # 环境变量覆盖配置
    env_overrides = {
        'api': {
            'api_key': os.getenv('PREDICT_API_KEY', config.get('api', {}).get('api_key', ''))
        },
        'opinion': {
            'base_url': config.get('opinion', {}).get('base_url', 'https://proxy.opinion.trade:8443/openapi'),
            'api_key': os.getenv('OPINION_API_KEY', config.get('opinion', {}).get('api_key', '')),
            'cache_seconds': int(config.get('opinion', {}).get('cache_seconds', 60))
        },
        'opinion_poly': {
            'min_arbitrage_threshold': float(os.getenv('OPINION_POLY_THRESHOLD', config.get('opinion_poly', {}).get('min_arbitrage_threshold', 2.0))),
            'min_confidence': float(config.get('opinion_poly', {}).get('min_confidence', 0.2))
        },
        'notification': {
            'telegram': {
                'enabled': os.getenv('TELEGRAM_ENABLED', 'true').lower() == 'true',
                'bot_token': os.getenv('TELEGRAM_BOT_TOKEN', config.get('notification', {}).get('telegram', {}).get('bot_token', '')),
                'chat_id': os.getenv('TELEGRAM_CHAT_ID', config.get('notification', {}).get('telegram', {}).get('chat_id', ''))
            }
        },
        'arbitrage': {
            'enabled': os.getenv('ARBITRAGE_ENABLED', 'true').lower() == 'true',
            'min_arbitrage_threshold': float(os.getenv('MIN_ARBITRAGE_THRESHOLD', config.get('arbitrage', {}).get('min_arbitrage_threshold', 2.0))),
            'scan_interval': int(os.getenv('SCAN_INTERVAL', config.get('arbitrage', {}).get('scan_interval', 10))),
            'cooldown_minutes': int(os.getenv('COOLDOWN_MINUTES', config.get('arbitrage', {}).get('cooldown_minutes', 5)))
        }
    }

    # 合并配置
    for key, value in env_overrides.items():
        if key not in config:
            config[key] = {}
        config[key].update(value)

    return config
