import logging

from agent_runtime import AgentRuntime
from channel import OutgoingMessage
from config import (
    load_reminder_config,
    load_telegram_config,
)
from logging_config import configure_logging
from reminder_scheduler import (
    ReminderCommandError,
    ReminderScheduler,
    parse_reminder_command,
)
from session_store import SessionStoreError
from telegram_api import TelegramAPIError
from telegram_channel import TelegramChannel


logger = logging.getLogger(__name__)


def main() -> int:
    """使用 Telegram 渠道运行 Agent 和受控定时提醒。"""
    configure_logging()
    scheduler: ReminderScheduler | None = None

    try:
        telegram_config = load_telegram_config()
        channel = TelegramChannel(telegram_config)
        agent = AgentRuntime()
        scheduler = ReminderScheduler(
            channel,
            load_reminder_config(),
        )
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

    for notice in agent.get_startup_notices():
        logger.info("Telegram Agent 启动状态: %s", notice)

    print("Telegram Agent 已启动。按 Ctrl+C 停止。")

    try:
        while True:
            incoming_message = channel.receive_message()

            try:
                reminder_request = parse_reminder_command(
                    incoming_message.text.strip()
                )
            except ReminderCommandError as error:
                channel.send_message(
                    OutgoingMessage(
                        conversation_id=incoming_message.conversation_id,
                        text=f"提醒命令错误：{error}",
                    )
                )
                continue

            if reminder_request is not None:
                try:
                    scheduler.schedule(
                        incoming_message.conversation_id,
                        reminder_request,
                    )
                except ReminderCommandError as error:
                    channel.send_message(
                        OutgoingMessage(
                            conversation_id=incoming_message.conversation_id,
                            text=f"无法创建提醒：{error}",
                        )
                    )
                    continue

                channel.send_message(
                    OutgoingMessage(
                        conversation_id=incoming_message.conversation_id,
                        text=(
                            "提醒已创建，将在 "
                            f"{reminder_request.delay_seconds} 秒后发送。"
                        ),
                    )
                )
                continue

            # 只有普通聊天才交给 Agent，因此提醒命令不会污染主会话。
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
    finally:
        if scheduler is not None:
            scheduler.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())