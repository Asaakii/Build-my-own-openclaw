from datetime import timedelta
from pathlib import Path

import pytest

from config import ReminderConfig
from persistent_reminder_service import (
    PersistentReminderService,
    ReminderTaskError,
    utc_now,
)
from sqlite_state_store import SQLiteStateStore


def create_store(tmp_path: Path) -> SQLiteStateStore:
    """每个测试使用独立数据库，不影响真实 Gateway 任务。"""
    return SQLiteStateStore(tmp_path / "reminders.db")


def create_service(
    store: SQLiteStateStore,
    delivered: list[tuple[dict[str, str], str]],
) -> PersistentReminderService:
    """使用假投递器验证状态机，不向真实 Telegram 发送消息。"""
    return PersistentReminderService(
        store,
        ReminderConfig(
            max_delay_seconds=60,
            max_active_tasks=2,
        ),
        delivery_sender=lambda delivery, content: delivered.append(
            (delivery, content)
        ),
    )


def create_reminder(
    service: PersistentReminderService,
) -> None:
    """创建一条固定的安全测试提醒。"""
    service.create_reminder(
        "telegram:123",
        1,
        "提醒验证",
        {
            "channel": "telegram",
            "conversation_id": "123",
        },
    )


def test_due_task_is_delivered_once_and_becomes_delivered(
    tmp_path: Path,
) -> None:
    """同一任务领取后只能投递一次，后续扫描不会再次发送。"""
    delivered: list[tuple[dict[str, str], str]] = []
    service = create_service(create_store(tmp_path), delivered)
    create_reminder(service)

    due_time = utc_now() + timedelta(seconds=2)
    assert service.process_due_tasks(due_time) == 1
    assert service.process_due_tasks(due_time) == 0
    assert delivered == [
        (
            {"channel": "telegram", "conversation_id": "123"},
            "提醒验证",
        )
    ]
    assert service.list_tasks()[0].status == "delivered"


def test_pending_task_survives_service_restart(
    tmp_path: Path,
) -> None:
    """Gateway 重启前未到期的提醒应从同一 SQLite 数据库恢复。"""
    store = create_store(tmp_path)
    first_deliveries: list[tuple[dict[str, str], str]] = []
    first_service = create_service(store, first_deliveries)
    create_reminder(first_service)

    recovered_deliveries: list[tuple[dict[str, str], str]] = []
    recovered_service = create_service(store, recovered_deliveries)
    assert recovered_service.process_due_tasks(
        utc_now() + timedelta(seconds=2)
    ) == 1
    assert first_deliveries == []
    assert len(recovered_deliveries) == 1
    assert recovered_service.list_tasks()[0].status == "delivered"


def test_delivery_failure_becomes_failed_without_automatic_retry(
    tmp_path: Path,
) -> None:
    """发送异常后任务终结为 failed，避免外部消息被重复发送。"""
    attempts: list[str] = []

    def fail_delivery(
        _delivery: dict[str, str],
        content: str,
    ) -> None:
        attempts.append(content)
        raise RuntimeError("测试发送失败")

    service = PersistentReminderService(
        create_store(tmp_path),
        ReminderConfig(
            max_delay_seconds=60,
            max_active_tasks=2,
        ),
        delivery_sender=fail_delivery,
    )
    create_reminder(service)

    due_time = utc_now() + timedelta(seconds=2)
    assert service.process_due_tasks(due_time) == 1
    assert service.process_due_tasks(due_time) == 0
    assert attempts == ["提醒验证"]
    assert service.list_tasks()[0].status == "failed"


def test_invalid_delivery_and_active_task_limit_are_rejected(
    tmp_path: Path,
) -> None:
    """提醒不能绕过 Telegram 私聊限制，也不能无限积压。"""
    delivered: list[tuple[dict[str, str], str]] = []
    service = create_service(create_store(tmp_path), delivered)

    with pytest.raises(ReminderTaskError, match="投递目标"):
        service.create_reminder(
            "telegram:123",
            1,
            "提醒验证",
            {
                "channel": "telegram",
                "conversation_id": "../unsafe",
            },
        )

    create_reminder(service)
    create_reminder(service)

    with pytest.raises(ReminderTaskError, match="数量已达上限"):
        create_reminder(service)
