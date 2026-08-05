import logging

from llm_client import LLMClientError, run_agent_turn
from logging_config import configure_logging
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

    print("个人 Agent 已启动。输入 '/exit' 或 '/quit' 退出。")

    # SOUL.md 的内容作为 system 消息，保证模型在本次会话中都能看到
    # 在后续的每次请求中，都会把历史消息和 system 消息一起发送给模型
    conversation: list[dict[str, object]] = [
        {
            "role": "system",
            "content": soul
        }
    ]

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

        print(f"\nAgent: {answer}")


if __name__ == "__main__":
    raise SystemExit(main())