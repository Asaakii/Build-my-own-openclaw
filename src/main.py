import logging

from agent_runtime import AgentRuntime
from channel import (
    ChannelClosed,
    MessageChannel,
    OutgoingMessage,
)
from logging_config import configure_logging
from session_store import SessionStoreError
from soul import load_soul
from terminal_channel import TerminalChannel


# 先只支持两个退出命令， 其他输入都当做普通聊天内容
EXIT_COMMANDS = {"/exit", "/quit"}
logger = logging.getLogger(__name__)


def send_notice(channel: MessageChannel, text: str) -> None:
    """发送不属于 Agent 正式回答的提示，例如启动和错误信息。"""
    channel.send_message(
        OutgoingMessage(
            conversation_id=None,
            text=text,
            is_reply=False,
        )
    )


def main() -> int:
    """通过终端渠道运行与渠道无关的 Agent 核心。"""
    configure_logging()
    logger.info("个人 Agent 启动")  # 启动时记录一条日志，方便排查问题

    # 当前仍使用终端；后续替换为 Telegram 时，核心逻辑不需要重写。
    channel: MessageChannel = TerminalChannel()

    try:
        agent = AgentRuntime()
    except (
        FileNotFoundError,
        ValueError,
        SessionStoreError,
    ) as error:
        logger.error("Agent 启动失败: %s", error)
        send_notice(channel, f"无法启动个人 Agent: {error}")
        return 1

    send_notice(channel, "个人 Agent 已启动。输入 '/exit' 或 '/quit' 退出。")

    for notice in agent.get_startup_notices():
        send_notice(channel, notice)

    while True:
        try:
            incoming_message = channel.receive_message()
        except ChannelClosed:
            logger.info("用户中断聊天")
            send_notice(channel, "\n聊天已结束。")
            return 0

        user_message = incoming_message.text.strip()

        if user_message.lower() in EXIT_COMMANDS:
            logger.info("用户退出聊天")
            send_notice(channel, "聊天已结束。")
            return 0

        for outgoing_message in agent.handle_message(incoming_message):
            channel.send_message(outgoing_message)


if __name__ == "__main__":
    raise SystemExit(main())