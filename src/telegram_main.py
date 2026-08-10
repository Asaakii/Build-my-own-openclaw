import logging

from channel import OutgoingMessage
from config import (
    load_gateway_config,
    load_telegram_config,
)
from gateway_client import GatewayClientError
from logging_config import configure_logging
from telegram_api import TelegramAPIError
from telegram_channel import TelegramChannel
from telegram_gateway_adapter import forward_telegram_message


logger = logging.getLogger(__name__)

GATEWAY_UNAVAILABLE_REPLY = (
    "本机 Gateway 暂不可用，请确认服务正在运行后再试。"
)


def main() -> int:
    """运行 Telegram 渠道适配器，所有聊天处理都交给 Gateway。"""
    configure_logging()

    try:
        telegram_config = load_telegram_config()
        gateway_config = load_gateway_config()
        channel = TelegramChannel(telegram_config)
    except (ValueError, TelegramAPIError) as error:
        logger.error(
            "Telegram Gateway 渠道启动失败: error_type=%s",
            type(error).__name__,
        )
        print(f"无法启动 Telegram Gateway 渠道: {error}")
        return 1

    logger.info("Telegram Gateway 渠道已启动")
    print("Telegram Gateway 渠道已启动。按 Ctrl+C 停止。")

    try:
        while True:
            incoming_message = channel.receive_message()

            try:
                outgoing_message = forward_telegram_message(
                    gateway_config,
                    incoming_message,
                )
            except (ValueError, GatewayClientError) as error:
                # 不向 Telegram 回显 Gateway 内部错误、Token 或消息内容。
                logger.warning(
                    "Telegram Gateway 转发失败: error_type=%s",
                    type(error).__name__,
                )
                channel.send_message(
                    OutgoingMessage(
                        conversation_id=incoming_message.conversation_id,
                        text=GATEWAY_UNAVAILABLE_REPLY,
                        is_reply=False,
                    )
                )
                continue

            channel.send_message(outgoing_message)

    except KeyboardInterrupt:
        logger.info("Telegram Gateway 渠道被本机中断")
        print("\nTelegram Gateway 渠道已结束。")
        return 0
    except TelegramAPIError as error:
        logger.warning(
            "Telegram 渠道运行失败: error_type=%s",
            type(error).__name__,
        )
        print("Telegram 渠道运行失败，请检查网络或 Bot 配置。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
