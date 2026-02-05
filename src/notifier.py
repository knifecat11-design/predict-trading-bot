"""
Telegram 通知模块
"""

import requests
import logging
from typing import Optional
from dataclasses import dataclass

from .arbitrage_monitor import ArbitrageOpportunity


@dataclass
class TelegramConfig:
    """Telegram 配置"""
    enabled: bool
    bot_token: str
    chat_id: str


class TelegramNotifier:
    """
    Telegram 通知器
    """

    def __init__(self, config: dict):
        tg_config = config.get('notification', {}).get('telegram', {})

        self.config = TelegramConfig(
            enabled=tg_config.get('enabled', False),
            bot_token=tg_config.get('bot_token', ''),
            chat_id=tg_config.get('chat_id', '')
        )

        self.logger = logging.getLogger(__name__)

        if not self.config.enabled:
            self.logger.warning("Telegram 通知未启用")
        elif not self.config.bot_token or not self.config.chat_id:
            self.logger.warning("Telegram 未配置 bot_token 或 chat_id")

    def send_arbitrage_alert(self, opportunity: 'ArbitrageOpportunity'):
        """
        发送套利机会通知

        Args:
            opportunity: 套利机会
        """
        if not self.config.enabled:
            return

        # 格式化消息
        message = self._format_arbitrage_message(opportunity)

        # 发送
        self._send_telegram(message)

    def send_test_message(self):
        """发送测试消息"""
        if not self.config.enabled:
            print("Telegram 通知未启用")
            return

        message = """🔔 套利监控系统测试

如果你看到这条消息，说明 Telegram 配置正确！

系统已准备就绪，开始监控套利机会..."""

        self._send_telegram(message)

    def _format_arbitrage_message(self, opp: 'ArbitrageOpportunity') -> str:
        """
        格式化套利机会消息

        格式要求：
        - 标注平台1买Yes还是No
        - 标注平台2买Yes还是No
        - 利差（套利空间）
        - 市场名称
        - 两平台对应价格
        """

        # 套利方向描述
        direction_desc = f"{opp.platform1_name} {opp.platform1_action} + {opp.platform2_name} {opp.platform2_action}"

        message = f"""━━━━━━━━━━━━━━━━━━━━━
📊 市场名称: {opp.market_name}

📈 利差: {opp.arbitrage_percent:.2f}%
💵 组合价格: {opp.combined_price:.1f}%

🔄 套利方向: {direction_desc}

━━━━━━━━━━━━━━━━━━━━━

📍 {opp.platform1_name}
  操作: {opp.platform1_action}
  Yes价格: {opp.platform1_yes_price:.1f}%
  No价格: {opp.platform1_no_price:.1f}%

📍 {opp.platform2_name}
  操作: {opp.platform2_action}
  Yes价格: {opp.platform2_yes_price:.1f}%
  No价格: {opp.platform2_no_price:.1f}%

━━━━━━━━━━━━━━━━━━━━━

⏰ 时间: {self._format_timestamp(opp.timestamp)}
⚡ 请尽快手动执行套利！"""

        return message

    def _send_telegram(self, message: str):
        """
        发送 Telegram 消息

        Args:
            message: 消息内容
        """
        if not self.config.bot_token or not self.config.chat_id:
            self.logger.error("Telegram bot_token 或 chat_id 未配置")
            return

        url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"

        try:
            # 转义消息中的特殊字符
            escaped_message = message.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

            response = requests.post(url, json={
                'chat_id': self.config.chat_id,
                'text': escaped_message,
                'parse_mode': 'HTML'
            }, timeout=15)

            result = response.json()

            if result.get('ok'):
                self.logger.info("Telegram 推送成功")
            else:
                error_desc = result.get('description', 'Unknown error')
                error_code = result.get('error_code', 'N/A')
                self.logger.error(f"Telegram 推送失败 [{error_code}]: {error_desc}")

        except requests.exceptions.Timeout:
            self.logger.error("Telegram 推送超时")
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Telegram 网络错误: {e}")
        except Exception as e:
            self.logger.error(f"Telegram 推送出错: {type(e).__name__}: {e}")

    def _format_timestamp(self, timestamp: float) -> str:
        """格式化时间戳"""
        import datetime
        dt = datetime.datetime.fromtimestamp(timestamp)
        return dt.strftime('%Y-%m-%d %H:%M:%S')


# 保留微信通知作为备用（不使用）
class WeChatNotifier:
    """
    微信推送通知器（备用，不推荐使用）
    """

    def __init__(self, config: dict):
        self.logger = logging.getLogger(__name__)
        self.logger.warning("微信推送已停用，请使用 Telegram")

    def send_arbitrage_alert(self, opportunity: 'ArbitrageOpportunity'):
        pass

    def send_test_message(self):
        pass
