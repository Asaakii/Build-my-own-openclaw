from dataclasses import dataclass
import logging
from threading import Lock, Timer
from uuid import uuid4

from channel import MessageChannel, OutgoingMessage
from config import ReminderConfig


logger = logging.getLogger(__name__)

REMINDER_COMMAND_PREFIX = "/remind "
MAX_REMINDER_TEXT_LENGTH = 500


class ReminderCommandError(ValueError):
    """表示提醒命令格式或参数不符合限制。"""


@dataclass(frozen=True)
class ReminderRequest:
    """保存一条经过校验、可被调度的提醒请求。"""

    delay_seconds: int
    content: str


def parse_reminder_command(text: str) -> ReminderRequest | None:
    """解析 /remind 秒数 内容；普通消息返回 None。"""
    if not text.startswith("/remind"):
        return None

    if not text.startswith(REMINDER_COMMAND_PREFIX):
        raise ReminderCommandError(
            "格式应为：/remind 秒数 提醒内容"
        )

    arguments = text.removeprefix(
        REMINDER_COMMAND_PREFIX
    ).strip().split(maxsplit=1)

    if len(arguments) != 2:
        raise ReminderCommandError(
            "格式应为：/remind 秒数 提醒内容"
        )

    delay_text, content = arguments

    try:
        delay_seconds = int(delay_text)
    except ValueError as error:
        raise ReminderCommandError(
            "提醒秒数必须是正整数"
        ) from error

    content = content.strip()

    if delay_seconds <= 0:
        raise ReminderCommandError(
            "提醒秒数必须大于 0"
        )

    if not content:
        raise ReminderCommandError(
            "提醒内容不能为空"
        )

    if len(content) > MAX_REMINDER_TEXT_LENGTH:
        raise ReminderCommandError(
            f"提醒内容不能超过 {MAX_REMINDER_TEXT_LENGTH} 个字符"
        )

    return ReminderRequest(
        delay_seconds=delay_seconds,
        content=content,
    )


class ReminderScheduler:
    """管理当前进程内的短时提醒任务。"""

    def __init__(
        self,
        channel: MessageChannel,
        config: ReminderConfig,
    ) -> None:
        self.channel = channel
        self.config = config
        self.lock = Lock()
        self.timers: dict[str, Timer] = {}

    def schedule(
        self,
        conversation_id: str,
        request: ReminderRequest,
    ) -> str:
        """创建一个受资源限制的提醒，并返回任务标识。"""
        if request.delay_seconds > self.config.max_delay_seconds:
            raise ReminderCommandError(
                "提醒时间超过当前允许上限"
            )

        task_id = uuid4().hex

        with self.lock:
            if len(self.timers) >= self.config.max_active_tasks:
                raise ReminderCommandError(
                    "当前提醒任务数量已达上限"
                )

            timer = Timer(
                request.delay_seconds,
                self.deliver_reminder,
                args=(
                    task_id,
                    conversation_id,
                    request.content,
                ),
            )
            # 程序停止后，提醒不应阻止进程退出。
            timer.daemon = True
            self.timers[task_id] = timer
            timer.start()

        # 只记录任务标识和等待时间，不记录用户提醒内容。
        logger.info(
            "提醒任务已创建: delay_seconds=%d",
            request.delay_seconds,
        )
        return task_id

    def deliver_reminder(
        self,
        task_id: str,
        conversation_id: str,
        content: str,
    ) -> None:
        """在计时结束后发送提醒，不调用模型或修改主会话。"""
        try:
            self.channel.send_message(
                OutgoingMessage(
                    conversation_id=conversation_id,
                    text=f"提醒：{content}",
                    is_reply=False,
                )
            )
            logger.info("提醒任务已发送")
        except Exception as error:
            # 定时器运行在线程中，必须自行捕获异常并留下脱敏日志。
            logger.error(
                "提醒任务发送失败: error_type=%s",
                type(error).__name__,
            )
        finally:
            with self.lock:
                self.timers.pop(task_id, None)

    def shutdown(self) -> None:
        """取消尚未触发的提醒，避免退出后仍保留后台任务。"""
        with self.lock:
            pending_timers = list(self.timers.values())
            self.timers.clear()

        for timer in pending_timers:
            timer.cancel()

        if pending_timers:
            logger.info(
                "已取消未完成提醒任务: task_count=%d",
                len(pending_timers),
            )