from pathlib import Path

import session_store


def configure_temporary_session_file(
    monkeypatch,
    tmp_path: Path,
) -> Path:
    """将会话模块指向临时文件，避免测试真实聊天记录。"""
    session_file = tmp_path / "default.jsonl"

    monkeypatch.setattr(
        session_store,
        "SESSION_FILE",
        session_file,
    )
    monkeypatch.setattr(
        session_store,
        "TEMP_SESSION_FILE",
        session_file.with_name("default.jsonl.tmp"),
    )

    return session_file


def test_session_messages_can_be_saved_and_loaded(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """已完成的用户和助手消息应能被恢复。"""
    configure_temporary_session_file(monkeypatch, tmp_path)

    messages = [
        {"role": "user", "content": "测试问题"},
        {"role": "assistant", "content": "测试回答"},
    ]

    session_store.append_session_messages(messages)
    loaded = session_store.load_session_messages()

    assert loaded.messages == messages
    assert loaded.summary is None
    assert loaded.skipped_lines == 0


def test_session_store_skips_corrupted_line(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """损坏 JSON 不应阻止后续有效会话被恢复。"""
    session_file = configure_temporary_session_file(
        monkeypatch,
        tmp_path,
    )
    session_file.write_text(
        "这不是 JSON\n"
        '{"role": "user", "content": "有效消息"}\n',
        encoding="utf-8",
    )

    loaded = session_store.load_session_messages()

    assert loaded.messages == [
        {"role": "user", "content": "有效消息"}
    ]
    assert loaded.skipped_lines == 1