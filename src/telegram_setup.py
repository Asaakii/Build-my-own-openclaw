from config import (
    load_telegram_connection_config,
)
from logging_config import configure_logging
from telegram_api import (
    TelegramAPIError,
    get_bot_info,
    get_recent_sender_ids,
)


def main() -> int:
    """验证 Bot Token，并显示最近向 Bot 发消息的用户 ID。"""
    configure_logging()

    try:
        config = load_telegram_connection_config()
        bot_info = get_bot_info(config)
        sender_ids = get_recent_sender_ids(config)
    except (ValueError, TelegramAPIError) as error:
        print(f"Telegram 连接验证失败：{error}")
        return 1

    print("Telegram Bot 连接验证成功。")

    if bot_info.username:
        print(f"Bot 用户名：@{bot_info.username}")

    if not sender_ids:
        print("没有读取到用户 ID。请先向 Bot 发送 /start 后重新运行。")
        return 0

    print("最近更新中的用户 ID：")

    for sender_id in sender_ids:
        print(sender_id)

    print("请在 .env 中添加自己的 Telegram 用户 ID：")
    print("TELEGRAM_ALLOWED_USER_ID=上面对应自己的数字")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())