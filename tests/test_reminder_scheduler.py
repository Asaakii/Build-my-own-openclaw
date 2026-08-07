import pytest

from reminder_scheduler import (
    ReminderCommandError,
    parse_reminder_command,
)


def test_reminder_command_is_parsed() -> None:
    """有效提醒命令应得到等待时间和提醒内容。"""
    request = parse_reminder_command(
        "/remind 10 定时提醒验证"
    )

    assert request is not None
    assert request.delay_seconds == 10
    assert request.content == "定时提醒验证"


def test_reminder_command_rejects_zero_seconds() -> None:
    """零秒提醒不应创建后台任务。"""
    with pytest.raises(
        ReminderCommandError,
        match="必须大于 0",
    ):
        parse_reminder_command("/remind 0 无效验证")