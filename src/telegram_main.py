import logging

from agent_runtime import AgentRuntime
from config import load_telegram_config
from logging_config import configure_logging
from session_store import SessionStoreError
from soul import load_soul
from telegram_api import TelegramAPIError
from telegram_channel import TelegramChannel


logger = logging.getLogger(__name__)


def main() -> int:
    """使用 Telegram 渠道运行单用户 Agent。"""
    configure_logging()

    try:
        telegram_config = load_telegram_config()
        channel = TelegramChannel(telegram_config)
        agent = AgentRuntime()
    except (
        FileNotFoundError,
        ValueError,
        SessionStoreError,
        TelegramAPIError,
    ) as error:
        logger.error(
            "Telegram Agent 启动失败: error_type=%s",
            type(error).__name__,
        )
        print(f"无法启动 Telegram Agent: {error}")
        return 1

    logger.info("Telegram Agent 已启动")

    # 启动状态只写本机日志，不会无目标地发送到 Telegram。
    for notice in agent.get_startup_notices():
        logger.info("Telegram Agent 启动状态: %s", notice)

    print("Telegram Agent 已启动。按 Ctrl+C 停止。")

    try:
        while True:
            incoming_message = channel.receive_message()

            for outgoing_message in agent.handle_message(incoming_message):
                channel.send_message(outgoing_message)
    except KeyboardInterrupt:
        logger.info("Telegram Agent 被本机中断")
        print("\nTelegram Agent 已结束。")
        return 0
    except TelegramAPIError as error:
        logger.warning("Telegram 渠道运行失败: %s", error)
        print(f"Telegram 渠道运行失败: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())