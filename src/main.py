from llm_client import LLMClientError, ask_model
from soul import load_soul


# 先只支持两个退出命令， 其他输入都当做普通聊天内容
EXIT_COMMANDS = {"/exit", "/quit"}


def main() -> int:
    """运行终端聊天循环，并在本次运行期间维护会话历史"""
    # 启动时读取人格文件，缺失或为空时不继续运行
    # 这样不会在未知人格配置下悄悄启动Agent
    try:
        soul = load_soul()
    except (FileNotFoundError, ValueError) as error:
        print(f"无法启动个人 Agent: {error}")
        return 1

    print("个人 Agent 已启动。输入 '/exit' 或 '/quit' 退出。")

    # SOUL.md 的内容作为 system 消息，保证模型在本次会话中都能看到
    # 在后续的每次请求中，都会把历史消息和 system 消息一起发送给模型
    conversation: list[dict[str, str]] = [
        {
            "role": "system",
            "content": soul
        }
    ]

    while True:
        try:
            user_message = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            # 用户按下 Ctrl+C，正常退出而不是显示错误堆栈
            print("\n聊天已结束。")
            return 0

        if not user_message:
            print("消息不能为空，请重新输入。")
            continue

        if user_message.lower() in EXIT_COMMANDS:
            print("聊天已结束。")
            return 0

        # 先记录用户问题，保证模型请求能看到它
        conversation.append(
            {
                "role": "user",
                "content": user_message,
            }
        )

        try:
            answer = ask_model(conversation)
        except (ValueError, LLMClientError) as error:
            # 调用失败时移除刚追加的用户消息，避免下一次请求时重复发送
            # 否则历史会以 user 消息结尾，下一轮角色顺序就会出错
            conversation.pop()
            print(f"模型请求失败: {error}")
            continue

        # 只有模型成功回答后，才把回答写入历史
        # 下一轮请求便能看到这条 assistant 消息，保证角色顺序正确
        conversation.append(
            {
                "role": "assistant",
                "content": answer,
            }
        )

        print(f"\nAgent: {answer}")


if __name__ == "__main__":
    raise SystemExit(main())