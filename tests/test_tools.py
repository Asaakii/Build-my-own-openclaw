from pathlib import Path

import pytest

import tools


def test_calculate_returns_expected_result() -> None:
    """安全计算工具应正确完成普通算术。"""
    assert tools.calculate("(23 * 17 + 6) / 5") == "79.4"


def test_calculate_rejects_code_shaped_input() -> None:
    """函数调用形态的输入必须被拒绝，绝不能执行。"""
    with pytest.raises(
        ValueError,
        match="只允许数字",
    ):
        tools.calculate('__import__("os").system("echo unsafe")')


def test_note_tool_reads_new_note_from_temporary_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """笔记读写只在 pytest 提供的临时目录中进行。"""
    notes_directory = (tmp_path / "notes").resolve()
    monkeypatch.setattr(
        tools,
        "NOTES_DIRECTORY",
        notes_directory,
    )

    tools.write_note(
        "test-note.md",
        "临时笔记内容",
    )

    assert tools.read_note("test-note.md") == "临时笔记内容"


def test_note_tool_rejects_path_escape() -> None:
    """包含路径的文件名必须被拒绝。"""
    with pytest.raises(
        ValueError,
        match="不能包含路径",
    ):
        tools.get_note_path("../SOUL.md")