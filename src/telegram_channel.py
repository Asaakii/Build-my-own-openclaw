import logging

from channel import IncomingMessage, OutgoingMessage
from config import TelegramConfig
from telegram_api import (
    MAX_TELEGRAM_TEXT_LENGTH,
    TelegramAPIError,
    get_updates,
    send_text,
)


logger = logging.getLogger(__name__)

TELEGRAM_CHANNEL_NAME = "telegram"
UNSUPPORTED_MESSAGE_TEXT = "当前仅支持文字消息。"
EXIT_COMMAND_TEXT = "Telegram Agent 会持续运行。请直接继续聊天。"


def is_allowed_private_sender(
    sender_id: object,
    chat_type: object,
    allowed_user_id: int,
) -> bool:
    """只允许配置中的用户从私聊向 Agent 发送消息。"""
    return (
        isinstance(sender_id, int)
        and sender_id == allowed_user_id
        and chat_type == "private"
    )


def split_telegram_text(text: str) -> list[str]:
    """按 Telegram 单条文本上限拆分长回复。"""
    return [
        text[start:start + MAX_TELEGRAM_TEXT_LENGTH]
        for start in range(0, len(text), MAX_TELEGRAM_TEXT_LENGTH)
    ]


class TelegramChannel:
    """用 Telegram 长轮询实现统一的消息渠道接口。"""

    def __init__(self, config: TelegramConfig) -> None:
        self.config = config
        # 仅在当前运行期间保存偏移量。
        # 下一次 getUpdates 携带更大的 offset 时才确认旧更新。
        self.next_offset: int | None = None

    def receive_message(self) -> IncomingMessage:
        """持续读取更新，直到拿到一条允许进入 Agent 的文字消息。"""
        while True:
            updates = get_updates(
                self.config,
                self.next_offset,
            )

            for update in updates:
                update_id = update.get("update_id")

                if not isinstance(update_id, int):
                    logger.warning("忽略缺少 update_id 的 Telegram 更新")
                    continue

                # 先准备下一次 offset；真正确认发生在下一次 getUpdates。
                self.next_offset = update_id + 1

                message = update.get("message")
                if not isinstance(message, dict):
                    logger.info("忽略非 message 类型的 Telegram 更新")
                    continue

                sender = message.get("from")
                chat = message.get("chat")

                if not isinstance(sender, dict) or not isinstance(chat, dict):
                    logger.warning("忽略结构不完整的 Telegram 消息")
                    continue

                sender_id = sender.get("id")
                chat_id = chat.get("id")
                chat_type = chat.get("type")

                if not is_allowed_private_sender(
                    sender_id,
                    chat_type,
                    self.config.allowed_user_id,
                ):
                    # 不记录用户 ID、用户名或消息内容，也不向陌生用户回复。
                    logger.warning("拒绝非白名单或非私聊 Telegram 消息")
                    continue

                if not isinstance(chat_id, int):
                    logger.warning("忽略缺少会话标识的 Telegram 消息")
                    continue

                text = message.get("text")

                if not isinstance(text, str):
                    # 白名单用户可以得到明确提示，但媒体不会进入模型。
                    send_text(
                        self.config,
                        str(chat_id),
                        UNSUPPORTED_MESSAGE_TEXT,
                    )
                    continue

                if text.lower() in {"/exit", "/quit"}:
                    # 退出命令仅属于终端程序，不能让 Telegram 用户停掉服务。
                    send_text(
                        self.config,
                        str(chat_id),
                        EXIT_COMMAND_TEXT,
                    )
                    continue

                if text == "/start":
                    send_text(
                        self.config,
                        str(chat_id),
                        "个人 Agent 已连接。请直接发送文字消息。",
                    )
                    continue

                return IncomingMessage(
                    channel_name=TELEGRAM_CHANNEL_NAME,
                    conversation_id=str(chat_id),
                    sender_id=str(sender_id),
                    text=text,
                )

    def send_message(self, message: OutgoingMessage) -> None:
        """将 Agent 回复拆分后发送回原 Telegram 私聊。"""
        if message.conversation_id is None:
            logger.warning("Telegram 出站消息缺少会话标识")
            return

        for text_part in split_telegram_text(message.text):
            send_text(
                self.config,
                message.conversation_id,
                text_part,
            )