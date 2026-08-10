from channel import IncomingMessage, OutgoingMessage
from config import GatewayConfig
from gateway_client import send_gateway_message


TELEGRAM_CHANNEL_NAME = "telegram"
TELEGRAM_SESSION_PREFIX = "telegram"


def build_telegram_session_id(
    conversation_id: str,
) -> str:
    """将 Telegram 私聊标识映射为稳定的 Gateway 会话标识。"""
    if not isinstance(conversation_id, str):
        raise ValueError("Telegram 会话标识无效")

    try:
        chat_id = int(conversation_id)
    except ValueError as error:
        raise ValueError("Telegram 会话标识无效") from error

    # 当前只接受私聊；Telegram 私聊标识应为正整数。
    if chat_id <= 0:
        raise ValueError("Telegram 会话标识无效")

    return f"{TELEGRAM_SESSION_PREFIX}:{chat_id}"


def forward_telegram_message(
    gateway_config: GatewayConfig,
    incoming_message: IncomingMessage,
) -> OutgoingMessage:
    """将允许进入的 Telegram 文本转给 Gateway，再保留原聊天目标返回。"""
    if incoming_message.channel_name != TELEGRAM_CHANNEL_NAME:
        raise ValueError("消息不是 Telegram 渠道消息")

    session_id = build_telegram_session_id(
        incoming_message.conversation_id
    )

    # Telegram 不创建 Agent、不读取 SQLite，只请求 Gateway。
    result = send_gateway_message(
        gateway_config,
        session_id,
        incoming_message.text,
    )

    return OutgoingMessage(
        # 回复仍要发到原始 Telegram chat_id，而不是 Gateway session_id。
        conversation_id=incoming_message.conversation_id,
        text=result.reply,
        is_reply=True,
    )