import pytest

import telegram_main
from channel import IncomingMessage, OutgoingMessage
from config import GatewayConfig, TelegramConfig
from gateway_client import GatewayClientError


TELEGRAM_CONFIG = TelegramConfig(
    bot_token="test-telegram-bot-token",
    request_timeout_seconds=30,
    poll_timeout_seconds=20,
    allowed_user_id=123,
)
GATEWAY_CONFIG = GatewayConfig(
    host="127.0.0.1",
    port=18790,
    token="test-token-with-at-least-thirty-two-characters",
)


class FakeTelegramChannel:
    """用一条确定性消息模拟渠道，并在下一轮正常停止。"""

    def __init__(self, incoming_message: IncomingMessage) -> None:
        self.incoming_message = incoming_message
        self.has_received_message = False
        self.sent_messages: list[OutgoingMessage] = []

    def receive_message(self) -> IncomingMessage:
        if not self.has_received_message:
            self.has_received_message = True
            return self.incoming_message

        raise KeyboardInterrupt

    def send_message(self, message: OutgoingMessage) -> None:
        self.sent_messages.append(message)


def make_incoming_message() -> IncomingMessage:
    """构造通过渠道白名单后的 Telegram 文本消息。"""
    return IncomingMessage(
        channel_name="telegram",
        conversation_id="123",
        sender_id="123",
        text="测试消息",
    )


def configure_main_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    channel: FakeTelegramChannel,
) -> None:
    """替换启动依赖，使主循环不读取私密配置或访问网络。"""
    monkeypatch.setattr(
        telegram_main,
        "configure_logging",
        lambda: None,
    )
    monkeypatch.setattr(
        telegram_main,
        "load_telegram_config",
        lambda: TELEGRAM_CONFIG,
    )
    monkeypatch.setattr(
        telegram_main,
        "load_gateway_config",
        lambda: GATEWAY_CONFIG,
    )
    monkeypatch.setattr(
        telegram_main,
        "TelegramChannel",
        lambda _config: channel,
    )


def test_telegram_main_forwards_only_through_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """主循环不创建 Agent，只转发消息并发送 Gateway 的回复。"""
    channel = FakeTelegramChannel(make_incoming_message())
    forwarded_arguments: list[tuple[GatewayConfig, IncomingMessage]] = []
    configure_main_dependencies(monkeypatch, channel)

    monkeypatch.setattr(
        telegram_main,
        "forward_telegram_message",
        lambda config, incoming_message: (
            forwarded_arguments.append((config, incoming_message))
            or OutgoingMessage(
                conversation_id="123",
                text="Gateway 回复",
            )
        ),
    )

    exit_code = telegram_main.main()

    assert exit_code == 0
    assert forwarded_arguments == [
        (GATEWAY_CONFIG, make_incoming_message())
    ]
    assert channel.sent_messages == [
        OutgoingMessage(
            conversation_id="123",
            text="Gateway 回复",
        )
    ]


def test_telegram_main_returns_safe_reply_when_gateway_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway 不可用时，渠道不能退回为本地 Agent。"""
    channel = FakeTelegramChannel(make_incoming_message())
    configure_main_dependencies(monkeypatch, channel)

    def raise_gateway_error(
        _config: GatewayConfig,
        _incoming_message: IncomingMessage,
    ) -> OutgoingMessage:
        raise GatewayClientError("Gateway 不可用")

    monkeypatch.setattr(
        telegram_main,
        "forward_telegram_message",
        raise_gateway_error,
    )

    exit_code = telegram_main.main()

    assert exit_code == 0
    assert channel.sent_messages == [
        OutgoingMessage(
            conversation_id="123",
            text=telegram_main.GATEWAY_UNAVAILABLE_REPLY,
            is_reply=False,
        )
    ]
