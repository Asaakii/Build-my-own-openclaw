import logging

from llm_client import LLMClientError, run_agent_turn
from logging_config import configure_logging
from session_store import (
    SessionStoreError,
    append_session_messages,
    load_session_messages,
)
from soul import load_soul


# 先只支持两个退出命令， 其他输入都当做普通聊天内容
EXIT_COMMANDS = {"/exit", "/quit"}
logger = logging.getLogger(__name__)


def main() -> int:
    """运行终端聊天循环，并在本次运行期间维护会话历史"""
    configure_logging()
    logger.info("个人 Agent 启动")  # 启动时记录一条日志，方便排查问题
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

    # system 消息始终使用当前 SOUL.md，不从旧会话中恢复。
    conversation: list[dict[str, object]] = [
        {
            "role": "system",
            "content": soul,
        },
        *loaded_session.messages,
    ]

    print("个人 Agent 已启动。输入 '/exit' 或 '/quit' 退出。")

    if loaded_session.messages:
        print(f"已恢复 {len(loaded_session.messages)} 条历史消息。")

    if loaded_session.skipped_lines:
        print(f"提示：已跳过 {loaded_session.skipped_lines} 条损坏的本地会话记录。")

    while True:
        try:
            user_message = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            logger.info("用户中断聊天")
            # 用户按下 Ctrl+C，正常退出而不是显示错误堆栈
            print("\n聊天已结束。")
            return 0

        if not user_message:
            # 不记录用户输入内容，只记录发生了空输入
            logger.info("拒绝空消息")
            print("消息不能为空，请重新输入。")
            continue

        if user_message.lower() in EXIT_COMMANDS:
            logger.info("用户退出聊天")
            print("聊天已结束。")
            return 0

        # 记录本轮开始前的长度。
        # 若工具调用中途失败，删除本轮所有新增消息，而不是只删一条。
        turn_start_index = len(conversation)

        # 先记录用户问题，保证模型请求能看到它
        conversation.append(
            {
                "role": "user",
                "content": user_message,
            }
        )

        try:
            answer = run_agent_turn(conversation)
        except (ValueError, LLMClientError) as error:
            # 工具循环可能已追加 assistant 与 tool 消息。
            # 因此回滚整轮，保持下一次会话历史完整。
            del conversation[turn_start_index:]
            logger.warning("Agent 回合失败: %s", error)
            print(f"模型请求失败: {error}")
            continue

        # 只有模型和工具都完成后，才把完整的一轮消息写入 JSONL。
        new_messages = conversation[turn_start_index:]

        try:
            append_session_messages(new_messages)
        except SessionStoreError as error:
            # 回答已经生成，仍然展示；只是明确提示本轮无法跨重启恢复。
            logger.error("会话保存失败: message_count=%d", len(new_messages))
            print(f"\nAgent: {answer}")
            print(f"提示：本轮回答已生成，但本地会话保存失败：{error}")
            continue

        print(f"\nAgent: {answer}")


if __name__ == "__main__":
    raise SystemExit(main())