from channel import (
    ChannelClosed,
    IncomingMessage,
    OutgoingMessage,
)


class TerminalChannel:
    """把终端输入输出适配为统一的消息渠道接口。"""

    CHANNEL_NAME = "terminal"
    CONVERSATION_ID = "terminal-default"
    SENDER_ID = "local-user"

    def receive_message(self) -> IncomingMessage:
        """读取终端输入，并转换成统一的入站消息。"""
        try:
            text = input("\n你: ")
        except (EOFError, KeyboardInterrupt) as error:
            # Ctrl+C 和输入结束都属于正常关闭，不向上抛出终端细节。
            raise ChannelClosed from error

        return IncomingMessage(
            channel_name=self.CHANNEL_NAME,
            conversation_id=self.CONVERSATION_ID,
            sender_id=self.SENDER_ID,
            text=text,
        )

    def send_message(self, message: OutgoingMessage) -> None:
        """把统一的出站消息显示到终端。"""
        prefix = "\nAgent: " if message.is_reply else ""
        print(f"{prefix}{message.text}")