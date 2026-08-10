import pytest

import telegram_gateway_adapter
from channel import IncomingMessage
from config import GatewayConfig
from gateway_client import (
    GatewayMessageResult,
    GatewayReminderResult,
)
from telegram_gateway_adapter import (
    build_telegram_session_id,
    forward_telegram_message,
)


TEST_GATEWAY_CONFIG = GatewayConfig(
    host="127.0.0.1",
    port=18790,
    token="test-token-with-at-least-thirty-two-characters",
)


def make_telegram_message(
    conversation_id: str = "123",
    channel_name: str = "telegram",
) -> IncomingMessage:
    """构造已通过渠道层校验的最小 Telegram 文本消息。"""
    return IncomingMessage(
        channel_name=channel_name,
        conversation_id=conversation_id,
        sender_id="123",
        text="测试转发消息",
    )


def test_build_telegram_session_id_uses_stable_prefix() -> None:
    """Telegram 私聊应映射到独立且稳定的 Gateway 会话。"""
    assert build_telegram_session_id("123") == "telegram:123"


@pytest.mark.parametrize("conversation_id", ["", "-123", "not-a-number"])
def test_build_telegram_session_id_rejects_invalid_chat_id(
    conversation_id: str,
) -> None:
    """路径形态、群聊形态或空标识不能进入 Gateway。"""
    with pytest.raises(ValueError, match="会话标识无效"):
        build_telegram_session_id(conversation_id)


def test_forward_telegram_message_uses_gateway_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """适配器只能通过 Gateway 转发，回复仍发送到原 Telegram 聊天。"""
    received_arguments: list[tuple[GatewayConfig, str, str]] = []

    monkeypatch.setattr(
        telegram_gateway_adapter,
        "send_gateway_message",
        lambda config, session_id, text: (
            received_arguments.append((config, session_id, text))
            or GatewayMessageResult(
                reply="Gateway 回复",
                compressed_message_count=0,
            )
        ),
    )

    outgoing_message = forward_telegram_message(
        TEST_GATEWAY_CONFIG,
        make_telegram_message(),
    )

    assert received_arguments == [
        (
            TEST_GATEWAY_CONFIG,
            "telegram:123",
            "测试转发消息",
        )
    ]
    assert outgoing_message.conversation_id == "123"
    assert outgoing_message.text == "Gateway 回复"


def test_forward_telegram_message_rejects_other_channel() -> None:
    """适配器不能被其他渠道误用为 Telegram 会话映射。"""
    with pytest.raises(ValueError, match="不是 Telegram"):
        forward_telegram_message(
            TEST_GATEWAY_CONFIG,
            make_telegram_message(channel_name="terminal"),
        )


def test_reminder_command_is_created_only_through_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram 的 /remind 只请求 Gateway，不持有进程内 Timer。"""
    received_arguments: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        telegram_gateway_adapter,
        "create_gateway_reminder",
        lambda *arguments: (
            received_arguments.append(arguments)
            or GatewayReminderResult(
                task_id="task-test",
                due_at="2026-03-07T00:00:10+00:00",
                status="pending",
            )
        ),
    )
    monkeypatch.setattr(
        telegram_gateway_adapter,
        "send_gateway_message",
        lambda *_arguments: pytest.fail("/remind 不应调用 Agent"),
    )
    incoming = IncomingMessage(
        channel_name="telegram",
        conversation_id="123",
        sender_id="123",
        text="/remind 10 定时提醒验证",
    )

    outgoing = forward_telegram_message(
        TEST_GATEWAY_CONFIG,
        incoming,
    )

    assert received_arguments == [
        (
            TEST_GATEWAY_CONFIG,
            "telegram:123",
            10,
            "定时提醒验证",
            {"channel": "telegram", "conversation_id": "123"},
        )
    ]
    assert outgoing.text == "提醒已创建，将在 10 秒后发送。"


def test_invalid_reminder_command_returns_safe_reply() -> None:
    """格式错误的 /remind 不进入 Gateway，也不会改走普通聊天。"""
    incoming = IncomingMessage(
        channel_name="telegram",
        conversation_id="123",
        sender_id="123",
        text="/remind 0 无效提醒",
    )

    outgoing = forward_telegram_message(
        TEST_GATEWAY_CONFIG,
        incoming,
    )

    assert outgoing.text == "提醒命令错误：提醒秒数必须大于 0"
