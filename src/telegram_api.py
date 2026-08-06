from dataclasses import dataclass
import logging

import httpx

from config import (
    TelegramConfig,
    TelegramConnectionConfig,
)


logger = logging.getLogger(__name__)

# Token 会拼接在请求地址中，因此绝不记录完整请求地址。
TELEGRAM_API_BASE_URL = "https://api.telegram.org"


class TelegramAPIError(RuntimeError):
    """表示 Telegram API 的受控失败，不暴露 Token 或原始响应。"""


@dataclass(frozen=True)
class TelegramBotInfo:
    """保存 getMe 返回的最小公开 Bot 信息。"""

    bot_id: int
    username: str | None


def call_bot_api(
    config: TelegramConnectionConfig,
    method_name: str,
    payload: dict[str, object],
) -> object:
    """调用 Telegram Bot API，并统一校验 HTTP 与 JSON 响应。"""
    url = (
        f"{TELEGRAM_API_BASE_URL}/bot"
        f"{config.bot_token}/{method_name}"
    )

    try:
        with httpx.Client(
            timeout=config.request_timeout_seconds,
            follow_redirects=False,
        ) as client:
            response = client.post(url, json=payload)
    except httpx.TimeoutException as error:
        logger.warning("Telegram API 请求超时")
        raise TelegramAPIError(
            "Telegram 请求超时，请稍后再试"
        ) from error
    except httpx.HTTPError as error:
        logger.warning("无法连接 Telegram API")
        raise TelegramAPIError(
            "无法连接 Telegram，请检查网络"
        ) from error

    try:
        response_data = response.json()
    except ValueError as error:
        logger.warning(
            "Telegram API 返回非 JSON: status_code=%d",
            response.status_code,
        )
        raise TelegramAPIError(
            "Telegram 服务返回了无效响应"
        ) from error

    if not isinstance(response_data, dict):
        raise TelegramAPIError("Telegram 服务返回了无效响应")

    if not response_data.get("ok"):
        logger.warning(
            "Telegram API 请求失败: status_code=%d",
            response.status_code,
        )

        if response.status_code in {401, 404}:
            raise TelegramAPIError(
                "Telegram Bot Token 无效或已失效"
            )

        raise TelegramAPIError(
            "Telegram API 请求失败，请检查网络或 Bot 设置"
        )

    return response_data.get("result")


def get_bot_info(
    config: TelegramConnectionConfig,
) -> TelegramBotInfo:
    """调用 getMe，验证 Token 并读取 Bot 的最小信息。"""
    result = call_bot_api(config, "getMe", {})

    if not isinstance(result, dict):
        raise TelegramAPIError("Telegram 返回的 Bot 信息无效")

    bot_id = result.get("id")
    username = result.get("username")

    if not isinstance(bot_id, int):
        raise TelegramAPIError("Telegram 返回的 Bot 信息无效")

    if username is not None and not isinstance(username, str):
        raise TelegramAPIError("Telegram 返回的 Bot 信息无效")

    return TelegramBotInfo(
        bot_id=bot_id,
        username=username,
    )


def get_recent_sender_ids(
    config: TelegramConnectionConfig,
) -> list[int]:
    """从最近的 message 更新中提取发送者 ID，不保存消息内容。"""
    result = call_bot_api(
        config,
        "getUpdates",
        {
            "timeout": 0,
            "allowed_updates": ["message"],
        },
    )

    if not isinstance(result, list):
        raise TelegramAPIError("Telegram 返回的更新列表无效")

    sender_ids: list[int] = []

    for update in result:
        if not isinstance(update, dict):
            continue

        message = update.get("message")
        if not isinstance(message, dict):
            continue

        sender = message.get("from")
        if not isinstance(sender, dict):
            continue

        sender_id = sender.get("id")
        if isinstance(sender_id, int) and sender_id not in sender_ids:
            sender_ids.append(sender_id)

    return sender_ids


MAX_TELEGRAM_TEXT_LENGTH = 4096


def get_updates(
    config: TelegramConfig,
    offset: int | None,
) -> list[dict[str, object]]:
    """通过长轮询读取 message 更新，不处理其他更新类型。"""
    payload: dict[str, object] = {
        "timeout": config.poll_timeout_seconds,
        "allowed_updates": ["message"],
    }

    if offset is not None:
        payload["offset"] = offset

    result = call_bot_api(config, "getUpdates", payload)

    if not isinstance(result, list):
        raise TelegramAPIError("Telegram 返回的更新列表无效")

    updates: list[dict[str, object]] = []

    for update in result:
        if isinstance(update, dict):
            updates.append(update)

    return updates


def send_text(
    config: TelegramConfig,
    conversation_id: str,
    text: str,
) -> None:
    """向指定 Telegram 私聊发送一段受长度限制的纯文本。"""
    if not conversation_id:
        raise TelegramAPIError("缺少 Telegram 会话标识")

    if not text:
        raise TelegramAPIError("不能发送空的 Telegram 消息")

    if len(text) > MAX_TELEGRAM_TEXT_LENGTH:
        raise TelegramAPIError("Telegram 单条消息超过长度上限")

    call_bot_api(
        config,
        "sendMessage",
        {
            "chat_id": conversation_id,
            "text": text,
        },
    )