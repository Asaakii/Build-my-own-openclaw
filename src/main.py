from llm_client import LLMClientError, ask_model


# 先只支持两个退出命令， 其他输入都当做普通聊天内容
EXIT_COMMANDS = {"/exit", "/quit"}


def main() -> int:
    """运行终端聊天循环。"""
    print("个人 Agent 已启动。输入 '/exit' 或 '/quit' 退出。")

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

        try:
            answer = ask_model(user_message)
        except (ValueError, LLMClientError) as error:
            # 预期错误只给用户简洁解释，底层细节留给后续日志系统
            print(f"模型请求失败: {error}")
            continue

        print(f"\nAgent: {answer}")


if __name__ == "__main__":
    raise SystemExit(main())