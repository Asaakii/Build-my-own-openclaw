import logging
from dataclasses import dataclass

from agent_runtime import (
    build_conversation,
    prepare_user_message,
)
from context_manager import maybe_compress_history
from llm_client import LLMClientError, run_agent_turn
from soul import load_soul
from sqlite_state_store import (
    SQLiteStateStore,
    StateStoreError,
)


logger = logging.getLogger(__name__)


class GatewayAgentError(RuntimeError):
    """表示 Gateway 无法安全完成一轮 Agent 处理。"""


@dataclass(frozen=True)
class GatewayAgentResult:
    """保存 API 可以返回的正式回答元数据。"""

    reply: str
    compressed_message_count: int


class GatewayAgentRuntime:
    """由 Gateway 独占的、按 session_id 隔离的 Agent 运行器。"""

    def __init__(
        self,
        state_store: SQLiteStateStore,
    ) -> None:
        """加载人格并保存状态层引用，不读取旧 JSONL 会话。"""
        self.state_store = state_store
        self.soul = load_soul()

    def log_tool_start(self, tool_name: str) -> None:
        """记录工具名称，不记录模型参数或工具结果。"""
        logger.info(
            "Gateway Agent 请求工具: tool_name=%s",
            tool_name,
        )

    def handle_text(
        self,
        session_id: str,
        text: str,
    ) -> GatewayAgentResult:
        """处理一条已通过 HTTP 校验的文本，并保存到指定 SQLite 会话。"""
        user_message = text.strip()

        if not user_message:
            raise GatewayAgentError("消息不能为空")

        try:
            stored_session = self.state_store.load_session(
                session_id
            )
            conversation = build_conversation(
                self.soul,
                stored_session.summary,
                stored_session.messages,
            )
            turn_start_index = len(conversation)

            model_message, authorized_memory_content = (
                prepare_user_message(user_message)
            )
            conversation.append(
                {
                    "role": "user",
                    "content": model_message,
                }
            )

            answer = run_agent_turn(
                conversation,
                authorized_memory_content,
                self.log_tool_start,
            )
        except (ValueError, LLMClientError, StateStoreError) as error:
            logger.warning(
                "Gateway Agent 回合失败: error_type=%s",
                type(error).__name__,
            )
            raise GatewayAgentError(
                "无法完成本轮 Agent 处理。"
            ) from error

        new_messages = conversation[turn_start_index:]

        try:
            self.state_store.append_messages(
                session_id,
                new_messages,
            )
        except StateStoreError as error:
            logger.error(
                "Gateway Agent 保存会话失败: error_type=%s",
                type(error).__name__,
            )
            raise GatewayAgentError(
                "本轮回答无法安全保存。"
            ) from error

        try:
            compression_result = maybe_compress_history(
                existing_summary=stored_session.summary,
                messages=(
                    stored_session.messages + new_messages
                ),
            )
        except (ValueError, LLMClientError) as error:
            # 回答和完整历史已经保存；压缩失败不应让用户丢失回答。
            logger.warning(
                "Gateway 会话压缩失败: error_type=%s",
                type(error).__name__,
            )
            return GatewayAgentResult(
                reply=answer,
                compressed_message_count=0,
            )

        if compression_result is None:
            return GatewayAgentResult(
                reply=answer,
                compressed_message_count=0,
            )

        try:
            self.state_store.replace_session_snapshot(
                session_id,
                compression_result.recent_messages,
                compression_result.summary,
            )
        except StateStoreError as error:
            # 完整消息已保存；压缩快照失败时保留完整历史更安全。
            logger.warning(
                "Gateway 压缩快照保存失败: error_type=%s",
                type(error).__name__,
            )
            return GatewayAgentResult(
                reply=answer,
                compressed_message_count=0,
            )

        return GatewayAgentResult(
            reply=answer,
            compressed_message_count=(
                compression_result.compressed_message_count
            ),
        )