from channel import IncomingMessage, OutgoingMessage
from context_manager import (
    build_summary_context_messages,
    maybe_compress_history,
)
from llm_client import LLMClientError, run_agent_turn
from session_store import (
    SessionStoreError,
    append_session_messages,
    load_session_messages,
    replace_session_snapshot,
)
from soul import load_soul


MEMORY_COMMAND_PREFIX = "/remember "


def build_conversation(
    soul: str,
    summary: str | None,
    session_messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    """组合人格、历史摘要和最近会话消息。"""
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


class AgentRuntime:
    """管理一个单用户 Agent 会话，不依赖终端或 Telegram。"""

    def __init__(self) -> None:
        """加载人格和本地会话，并准备运行时状态。"""
        self.soul = load_soul()

        loaded_session = load_session_messages()
        self.session_messages = loaded_session.messages
        self.current_summary = loaded_session.summary
        self.skipped_lines = loaded_session.skipped_lines

        self.conversation = build_conversation(
            self.soul,
            self.current_summary,
            self.session_messages,
        )

    def get_startup_notices(self) -> list[str]:
        """返回启动提示，让不同渠道决定是否展示。"""
        notices: list[str] = []

        if self.session_messages:
            notices.append(
                f"已恢复 {len(self.session_messages)} 条历史消息。"
            )

        if self.current_summary:
            notices.append("已加载一份历史摘要。")

        if self.skipped_lines:
            notices.append(
                "提示：已跳过 "
                f"{self.skipped_lines} 条损坏的本地会话记录。"
            )

        return notices

    def create_reply(
        self,
        incoming_message: IncomingMessage,
        text: str,
    ) -> OutgoingMessage:
        """创建发送回当前会话的正式回答。"""
        return OutgoingMessage(
            conversation_id=incoming_message.conversation_id,
            text=text,
            is_reply=True,
        )

    def create_notice(
        self,
        incoming_message: IncomingMessage,
        text: str,
    ) -> OutgoingMessage:
        """创建发送回当前会话的状态或错误提示。"""
        return OutgoingMessage(
            conversation_id=incoming_message.conversation_id,
            text=text,
            is_reply=False,
        )

    def handle_message(
        self,
        incoming_message: IncomingMessage,
    ) -> list[OutgoingMessage]:
        """处理一条渠道文本消息，并返回需要发送的回复。"""
        user_message = incoming_message.text.strip()

        if not user_message:
            return [
                self.create_reply(
                    incoming_message,
                    "消息不能为空，请重新输入。",
                )
            ]

        turn_start_index = len(self.conversation)

        try:
            model_message, authorized_memory_content = prepare_user_message(
                user_message
            )
        except ValueError as error:
            return [
                self.create_reply(
                    incoming_message,
                    f"命令使用错误：{error}",
                )
            ]

        self.conversation.append(
            {
                "role": "user",
                "content": model_message,
            }
        )

        try:
            answer = run_agent_turn(
                self.conversation,
                authorized_memory_content,
            )
        except (ValueError, LLMClientError) as error:
            # 工具调用失败时回滚完整的一轮，避免留下半截历史。
            del self.conversation[turn_start_index:]
            return [
                self.create_notice(
                    incoming_message,
                    f"模型请求失败: {error}",
                )
            ]

        new_messages = self.conversation[turn_start_index:]
        self.session_messages.extend(new_messages)

        try:
            append_session_messages(new_messages)
        except SessionStoreError as error:
            return [
                self.create_reply(incoming_message, answer),
                self.create_notice(
                    incoming_message,
                    "提示：本轮回答已生成，但本地会话保存失败："
                    f"{error}",
                ),
            ]

        try:
            compression_result = maybe_compress_history(
                existing_summary=self.current_summary,
                messages=self.session_messages,
            )
        except (ValueError, LLMClientError) as error:
            return [
                self.create_reply(incoming_message, answer),
                self.create_notice(
                    incoming_message,
                    f"提示：本轮已保存，但历史压缩失败：{error}",
                ),
            ]

        if compression_result is not None:
            try:
                replace_session_snapshot(
                    messages=compression_result.recent_messages,
                    summary=compression_result.summary,
                )
            except SessionStoreError as error:
                return [
                    self.create_reply(incoming_message, answer),
                    self.create_notice(
                        incoming_message,
                        "提示：本轮已保存，但压缩快照保存失败："
                        f"{error}",
                    ),
                ]

            self.current_summary = compression_result.summary
            self.session_messages = compression_result.recent_messages
            self.conversation = build_conversation(
                self.soul,
                self.current_summary,
                self.session_messages,
            )

            return [
                self.create_reply(incoming_message, answer),
                self.create_notice(
                    incoming_message,
                    "提示：会话已压缩，"
                    f"总结了 {compression_result.compressed_message_count} "
                    "条旧消息。",
                ),
            ]

        return [self.create_reply(incoming_message, answer)]