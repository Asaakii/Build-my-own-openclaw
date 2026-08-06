import logging

from channel import (
    ChannelClosed,
    IncomingMessage,
    MessageChannel,
    OutgoingMessage,
)
from context_manager import (
    build_summary_context_messages,
    maybe_compress_history,
)
from llm_client import LLMClientError, run_agent_turn
from logging_config import configure_logging
from session_store import (
    SessionStoreError,
    append_session_messages,
    load_session_messages,
    replace_session_snapshot,
)
from soul import load_soul
from terminal_channel import TerminalChannel


# 先只支持两个退出命令， 其他输入都当做普通聊天内容
EXIT_COMMANDS = {"/exit", "/quit"}
MEMORY_COMMAND_PREFIX = "/remember "
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


def send_reply(
    channel: MessageChannel,
    incoming_message: IncomingMessage,
    text: str,
) -> None:
    """将 Agent 回答发送回触发本轮对话的会话。"""
    channel.send_message(
        OutgoingMessage(
            conversation_id=incoming_message.conversation_id,
            text=text,
        )
    )


def build_conversation(
    soul: str,
    summary: str | None,
    session_messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    """组合当前人格、可选历史摘要和未压缩的最近消息。"""
    conversation: list[dict[str, object]] = [
        {
            "role": "system",
            "content": soul,
        }
    ]

    if summary:
        conversation.extend(build_summary_context_messages(summary))

    conversation.extend(session_messages)
    return conversation


def prepare_user_message(
    user_message: str,
) -> tuple[str, str | None]:
    """识别 /remember 命令，并返回模型消息和本轮授权内容。"""
    if not user_message.startswith(MEMORY_COMMAND_PREFIX):
        return user_message, None

    authorized_memory_content = user_message.removeprefix(
        MEMORY_COMMAND_PREFIX
    ).strip()

    if not authorized_memory_content:
        raise ValueError("/remember 后必须提供要保存的内容")

    model_message = (
        "用户已通过 /remember 明确授权保存以下长期记忆。"
        "请调用 save_memory 工具，且 content 必须保持原文：\n"
        f"{authorized_memory_content}"
    )

    return model_message, authorized_memory_content


def main() -> int:
    """运行终端聊天循环，并在本次运行期间维护会话历史"""
    configure_logging()
    logger.info("个人 Agent 启动")  # 启动时记录一条日志，方便排查问题

    # 当前仍使用终端；后续替换为 Telegram 时，核心逻辑不需要重写。
    channel: MessageChannel = TerminalChannel()

    # 启动时读取人格文件，缺失或为空时不继续运行
    # 这样不会在未知人格配置下悄悄启动Agent
    try:
        soul = load_soul()
    except (FileNotFoundError, ValueError) as error:
        logger.error("人格配置加载失败: %s", error)
        print(f"无法启动个人 Agent: {error}")
        return 1

    # 加载上次已成功完成的对话记录。
    try:
        loaded_session = load_session_messages()
    except SessionStoreError as error:
        logger.error("会话加载失败: %s", error)
        print(f"无法启动个人 Agent: {error}")
        return 1

    session_messages = loaded_session.messages
    current_summary = loaded_session.summary
    conversation = build_conversation(
        soul,
        current_summary,
        session_messages,
    )

    send_notice(channel, "个人 Agent 已启动。输入 '/exit' 或 '/quit' 退出。")

    if loaded_session.messages:
        send_notice(
            channel,
            f"已恢复 {len(loaded_session.messages)} 条历史消息。",
        )

    if current_summary:
        send_notice(channel, "已加载一份历史摘要。")

    if loaded_session.skipped_lines:
        send_notice(
            channel,
            f"提示：已跳过 {loaded_session.skipped_lines} 条损坏的本地会话记录。",
        )

    while True:
        try:
            incoming_message = channel.receive_message()
        except ChannelClosed:
            logger.info("用户中断聊天")
            send_notice(channel, "\n聊天已结束。")
            return 0

        # 空白字符也视为无效消息，但原始渠道消息仍保持为文本。
        user_message = incoming_message.text.strip()

        if not user_message:
            logger.info("拒绝空消息")
            send_reply(channel, incoming_message, "消息不能为空，请重新输入。")
            continue

        if user_message.lower() in EXIT_COMMANDS:
            logger.info("用户退出聊天")
            send_notice(channel, "聊天已结束。")
            return 0

        # 当前仍是单会话版本；conversation_id 已保留给后续真实渠道使用。
        turn_start_index = len(conversation)

        try:
            model_message, authorized_memory_content = prepare_user_message(
                user_message
            )
        except ValueError as error:
            send_reply(channel, incoming_message, f"命令使用错误：{error}")
            continue

        conversation.append(
            {
                "role": "user",
                "content": model_message,
            }
        )

        try:
            answer = run_agent_turn(
                conversation,
                authorized_memory_content,
            )
        except (ValueError, LLMClientError) as error:
            # 工具调用中途失败时，回滚整个本轮，保持历史角色顺序完整。
            del conversation[turn_start_index:]
            logger.warning("Agent 回合失败: %s", error)
            send_notice(channel, f"模型请求失败: {error}")
            continue

        new_messages = conversation[turn_start_index:]
        session_messages.extend(new_messages)

        try:
            append_session_messages(new_messages)
        except SessionStoreError as error:
            logger.error("会话保存失败: message_count=%d", len(new_messages))
            send_reply(channel, incoming_message, answer)
            send_notice(
                channel,
                f"提示：本轮回答已生成，但本地会话保存失败：{error}",
            )
            continue

        try:
            compression_result = maybe_compress_history(
                existing_summary=current_summary,
                messages=session_messages,
            )
        except (ValueError, LLMClientError) as error:
            logger.warning("会话压缩失败: %s", error)
            send_reply(channel, incoming_message, answer)
            send_notice(
                channel,
                f"提示：本轮已保存，但历史压缩失败：{error}",
            )
            continue

        if compression_result is not None:
            try:
                replace_session_snapshot(
                    messages=compression_result.recent_messages,
                    summary=compression_result.summary,
                )
            except SessionStoreError as error:
                logger.error("压缩快照保存失败: %s", error)
                send_reply(channel, incoming_message, answer)
                send_notice(
                    channel,
                    f"提示：本轮已保存，但压缩快照保存失败：{error}",
                )
                continue

            current_summary = compression_result.summary
            session_messages = compression_result.recent_messages
            conversation = build_conversation(
                soul,
                current_summary,
                session_messages,
            )

            send_reply(channel, incoming_message, answer)
            send_notice(
                channel,
                "提示：会话已压缩，"
                f"总结了 {compression_result.compressed_message_count} 条旧消息。",
            )
            continue

        send_reply(channel, incoming_message, answer)


if __name__ == "__main__":
    raise SystemExit(main())