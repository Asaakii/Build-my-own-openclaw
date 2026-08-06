from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class IncomingMessage:
    """统一表示来自任意渠道的一条文本消息。"""

    channel_name: str
    conversation_id: str
    sender_id: str
    text: str

    def __post_init__(self) -> None:
        """在进入 Agent 核心前，确认消息具备最小的文本结构。"""
        for field_name, value in (
            ("channel_name", self.channel_name),
            ("conversation_id", self.conversation_id),
            ("sender_id", self.sender_id),
            ("text", self.text),
        ):
            if not isinstance(value, str):
                raise ValueError(f"{field_name} 必须是文本")


@dataclass(frozen=True)
class OutgoingMessage:
    """统一表示 Agent 要发送到某个会话的文本回复。"""

    conversation_id: str | None
    text: str
    is_reply: bool = True


class ChannelClosed(Exception):
    """表示渠道被正常关闭，例如终端按下 Ctrl+C。"""


class MessageChannel(Protocol):
    """所有消息渠道都应遵守的最小接口。"""

    def receive_message(self) -> IncomingMessage:
        """接收一条用户文本消息。"""

    def send_message(self, message: OutgoingMessage) -> None:
        """向用户发送一条文本消息。"""