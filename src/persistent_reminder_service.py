import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from uuid import uuid4

from config import ReminderConfig, load_telegram_config
from sqlite_state_store import (
    SQLiteStateStore,
    StateStoreError,
    StoredTask,
    TaskInfo,
)
from telegram_api import TelegramAPIError, send_text


logger = logging.getLogger(__name__)

REMINDER_TASK_TYPE = "reminder"
TELEGRAM_DELIVERY_CHANNEL = "telegram"
MAX_REMINDER_TEXT_LENGTH = 500
MAX_DUE_TASKS_PER_SCAN = 20
TASK_SCAN_INTERVAL_SECONDS = 1.0


class ReminderTaskError(ValueError):
    """表示 Gateway 无法安全创建或投递提醒任务。"""


@dataclass(frozen=True)
class ReminderTaskResult:
    """保存创建提醒后可安全返回给客户端的元数据。"""

    task_id: str
    due_at: str
    status: str


def utc_now() -> datetime:
    """生成带时区的当前时间，避免本地时区影响到期比较。"""
    return datetime.now(timezone.utc)


def validate_delivery(
    delivery: object,
) -> dict[str, str]:
    """限制当前 MVP 的提醒投递目标为单用户 Telegram 私聊。"""
    if not isinstance(delivery, dict):
        raise ReminderTaskError("提醒投递目标无效")

    if set(delivery) != {"channel", "conversation_id"}:
        raise ReminderTaskError("提醒投递目标无效")

    channel = delivery["channel"]
    conversation_id = delivery["conversation_id"]

    if (
        channel != TELEGRAM_DELIVERY_CHANNEL
        or not isinstance(conversation_id, str)
        or not conversation_id.isdecimal()
        or int(conversation_id) <= 0
    ):
        raise ReminderTaskError("提醒投递目标无效")

    return {
        "channel": channel,
        "conversation_id": conversation_id,
    }


def send_telegram_reminder(
    delivery: dict[str, str],
    content: str,
) -> None:
    """由 Gateway 发送提醒；渠道适配器不再拥有定时器。"""
    safe_delivery = validate_delivery(delivery)
    telegram_config = load_telegram_config()
    send_text(
        telegram_config,
        safe_delivery["conversation_id"],
        f"提醒：{content}",
    )


class PersistentReminderService:
    """由 Gateway 独占的 SQLite 提醒任务创建、扫描与投递服务。"""

    def __init__(
        self,
        state_store: SQLiteStateStore,
        config: ReminderConfig,
        delivery_sender: Callable[[dict[str, str], str], None] = (
            send_telegram_reminder
        ),
        scan_interval_seconds: float = TASK_SCAN_INTERVAL_SECONDS,
    ) -> None:
        if scan_interval_seconds <= 0:
            raise ValueError("提醒扫描间隔必须大于 0")

        self.state_store = state_store
        self.config = config
        self.delivery_sender = delivery_sender
        self.scan_interval_seconds = scan_interval_seconds
        self.stop_event = Event()
        self.worker: Thread | None = None

    def create_reminder(
        self,
        session_id: str,
        delay_seconds: int,
        content: str,
        delivery: object,
    ) -> ReminderTaskResult:
        """先完整校验并持久化任务，再允许后台调度器领取。"""
        if (
            not isinstance(delay_seconds, int)
            or isinstance(delay_seconds, bool)
            or delay_seconds <= 0
        ):
            raise ReminderTaskError("提醒秒数必须大于 0")

        if delay_seconds > self.config.max_delay_seconds:
            raise ReminderTaskError("提醒时间超过当前允许上限")

        normalized_content = content.strip() if isinstance(content, str) else ""

        if not normalized_content:
            raise ReminderTaskError("提醒内容不能为空")

        if len(normalized_content) > MAX_REMINDER_TEXT_LENGTH:
            raise ReminderTaskError(
                f"提醒内容不能超过 {MAX_REMINDER_TEXT_LENGTH} 个字符"
            )

        safe_delivery = validate_delivery(delivery)

        try:
            active_task_count = self.state_store.count_tasks_with_status(
                {"pending", "delivering"}
            )
        except StateStoreError as error:
            raise ReminderTaskError("无法检查提醒任务状态") from error

        if active_task_count >= self.config.max_active_tasks:
            raise ReminderTaskError("当前提醒任务数量已达上限")

        due_at = utc_now() + timedelta(seconds=delay_seconds)
        task_id = uuid4().hex

        try:
            task_info = self.state_store.create_task(
                task_id=task_id,
                session_id=session_id,
                task_type=REMINDER_TASK_TYPE,
                payload={
                    "content": normalized_content,
                    "delivery": safe_delivery,
                },
                due_at=due_at.isoformat(),
            )
        except StateStoreError as error:
            raise ReminderTaskError("无法保存提醒任务") from error

        logger.info(
            "Gateway 提醒任务已创建: delay_seconds=%d",
            delay_seconds,
        )
        return ReminderTaskResult(
            task_id=task_info.task_id,
            due_at=task_info.due_at,
            status=task_info.status,
        )

    def list_tasks(self) -> list[TaskInfo]:
        """提供任务元数据，供 Gateway 状态接口安全展示。"""
        return self.state_store.list_tasks()

    def process_due_tasks(
        self,
        now: datetime | None = None,
    ) -> int:
        """领取到期任务并投递；每个任务只允许从 pending 领取一次。"""
        due_before = (now or utc_now()).isoformat()

        try:
            tasks = self.state_store.claim_due_tasks(
                REMINDER_TASK_TYPE,
                due_before,
                MAX_DUE_TASKS_PER_SCAN,
            )
        except StateStoreError as error:
            logger.error(
                "Gateway 提醒任务扫描失败: error_type=%s",
                type(error).__name__,
            )
            return 0

        for task in tasks:
            self.deliver_claimed_task(task)

        return len(tasks)

    def deliver_claimed_task(self, task: StoredTask) -> None:
        """投递已领取的提醒；失败后终结为 failed，避免静默重复发送。"""
        delivery = task.payload.get("delivery")
        content = task.payload.get("content")

        try:
            safe_delivery = validate_delivery(delivery)

            if not isinstance(content, str) or not content:
                raise ReminderTaskError("提醒内容无效")

            self.delivery_sender(safe_delivery, content)
        except (
            ReminderTaskError,
            TelegramAPIError,
            ValueError,
        ) as error:
            logger.warning(
                "Gateway 提醒任务发送失败: error_type=%s",
                type(error).__name__,
            )
            self.finish_task(task.task_id, "failed")
            return
        except Exception as error:
            # 发送器可来自外部网络；线程中必须捕获未知异常。
            logger.error(
                "Gateway 提醒任务异常: error_type=%s",
                type(error).__name__,
            )
            self.finish_task(task.task_id, "failed")
            return

        self.finish_task(task.task_id, "delivered")
        logger.info("Gateway 提醒任务已发送")

    def finish_task(self, task_id: str, status: str) -> None:
        """结束任务失败时只留下脱敏日志，不重新投递。"""
        try:
            self.state_store.complete_claimed_task(task_id, status)
        except StateStoreError as error:
            logger.error(
                "Gateway 提醒任务状态更新失败: error_type=%s",
                type(error).__name__,
            )

    def run_forever(self) -> None:
        """按固定间隔扫描任务；停止信号不会删除待执行记录。"""
        while not self.stop_event.is_set():
            self.process_due_tasks()
            self.stop_event.wait(self.scan_interval_seconds)

    def start(self) -> None:
        """启动单个后台扫描线程，重复调用不会创建第二个调度器。"""
        if self.worker is not None and self.worker.is_alive():
            return

        self.stop_event.clear()
        self.worker = Thread(
            target=self.run_forever,
            name="myclaw-reminder-service",
            daemon=True,
        )
        self.worker.start()
        logger.info("Gateway 提醒调度器已启动")

    def stop(self) -> None:
        """停止扫描线程，但保留未到期任务供下次 Gateway 启动恢复。"""
        self.stop_event.set()

        if self.worker is not None:
            self.worker.join(timeout=self.scan_interval_seconds + 1)

        logger.info("Gateway 提醒调度器已停止")
