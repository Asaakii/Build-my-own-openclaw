from llm_client import LLMClientError, ask_model


# 先只支持两个退出命令， 其他输入都当做普通聊天内容
EXIT_COMMANDS = {"/exit", "/quit"}

# 这是一条临时的 system 消息， 让模型知道当前的身份和行为规范
# 第 1.3 步会把它移到独立的 SOUL.md 文件，不再写死在代码里
SYSTEM_MESSAGE = {
    "role": "system",
    "content": "你是一个诚实、简洁的个人助手。你将根据用户的输入提供有用的信息和帮助。"
}


def main() -> int:
    """运行终端聊天循环。"""
    print("个人 Agent 已启动。输入 '/exit' 或 '/quit' 退出。")

    # 列表保存本次运行的完整会话
    # 初始只有 system 消息，后续会按 user 和 assistant 的顺序追加消息
    conversation: list[dict[str, str]] = [SYSTEM_MESSAGE]

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