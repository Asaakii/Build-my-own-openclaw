import sqlite3
from pathlib import Path

import pytest

from sqlite_state_store import (
    SQLiteStateStore,
    StateStoreError,
)


def create_store(tmp_path: Path) -> SQLiteStateStore:
    """每个测试使用独立数据库，不接触真实运行数据。"""
    return SQLiteStateStore(
        tmp_path / "myclaw-test.db"
    )


def test_sessions_are_isolated(
    tmp_path: Path,
) -> None:
    """不同 session_id 的消息必须互不混淆。"""
    store = create_store(tmp_path)

    store.append_messages(
        "local:first",
        [{"role": "user", "content": "第一会话"}],
    )
    store.append_messages(
        "local:second",
        [{"role": "user", "content": "第二会话"}],
    )

    first_session = store.load_session("local:first")
    second_session = store.load_session("local:second")

    assert first_session.messages == [
        {"role": "user", "content": "第一会话"}
    ]
    assert second_session.messages == [
        {"role": "user", "content": "第二会话"}
    ]


def test_snapshot_replaces_only_target_session(
    tmp_path: Path,
) -> None:
    """压缩快照只替换目标会话，不能影响另一会话。"""
    store = create_store(tmp_path)

    store.append_messages(
        "local:target",
        [{"role": "user", "content": "旧消息"}],
    )
    store.append_messages(
        "local:other",
        [{"role": "user", "content": "保留消息"}],
    )

    store.replace_session_snapshot(
        "local:target",
        [{"role": "assistant", "content": "新消息"}],
        "目标会话摘要",
    )

    target_session = store.load_session("local:target")
    other_session = store.load_session("local:other")

    assert target_session.summary == "目标会话摘要"
    assert target_session.messages == [
        {"role": "assistant", "content": "新消息"}
    ]
    assert other_session.messages == [
        {"role": "user", "content": "保留消息"}
    ]


def test_invalid_session_id_is_rejected(
    tmp_path: Path,
) -> None:
    """路径形式的 session_id 不能进入数据库。"""
    store = create_store(tmp_path)

    with pytest.raises(
        StateStoreError,
        match="session_id",
    ):
        store.append_messages(
            "../outside",
            [{"role": "user", "content": "无效"}],
        )


def test_corrupted_message_record_is_skipped(
    tmp_path: Path,
) -> None:
    """损坏的单条数据库记录不会阻止有效会话读取。"""
    store = create_store(tmp_path)
    store.initialize()

    database_path = tmp_path / "myclaw-test.db"

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO sessions (
                session_id,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?)
            """,
            (
                "local:broken",
                "2026-03-07T00:00:00+00:00",
                "2026-03-07T00:00:00+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO messages (
                session_id,
                message_index,
                payload_json,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                "local:broken",
                0,
                "{这不是 JSON",
                "2026-03-07T00:00:00+00:00",
            ),
        )

    loaded_session = store.load_session("local:broken")

    assert loaded_session.messages == []
    assert loaded_session.skipped_records == 1