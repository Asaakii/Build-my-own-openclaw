from pathlib import Path

import pytest

from legacy_session_migration import (
    LegacyMigrationError,
    create_legacy_backup,
    migrate_legacy_session,
)
from sqlite_state_store import SQLiteStateStore


def create_store(tmp_path: Path) -> SQLiteStateStore:
    """每个测试使用独立数据库，不接触真实运行数据。"""
    return SQLiteStateStore(tmp_path / "myclaw-test.db")


def test_legacy_session_is_backed_up_then_migrated(
    tmp_path: Path,
) -> None:
    """迁移应保留原文件、导入有效记录并跳过损坏行。"""
    legacy_file = tmp_path / "default.jsonl"
    original_content = (
        '{"record_type": "context_summary", "content": "旧摘要"}\n'
        '{"role": "user", "content": "旧问题"}\n'
        '损坏的 JSON\n'
        '{"role": "assistant", "content": "旧回答"}\n'
    )
    legacy_file.write_text(original_content, encoding="utf-8")

    result = migrate_legacy_session(
        create_store(tmp_path),
        legacy_file,
        tmp_path / "legacy-backups",
    )

    # 原文件与备份都必须保留，迁移不能删除或改写旧记录。
    assert legacy_file.read_text(encoding="utf-8") == original_content
    assert result.backup_path.read_text(
        encoding="utf-8"
    ) == original_content

    assert result.messages_imported == 2
    assert result.summary_imported is True
    assert result.skipped_lines == 1

    loaded_session = create_store(tmp_path).load_session("local:default")
    assert loaded_session.summary == "旧摘要"
    assert loaded_session.messages == [
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "旧回答"},
    ]


def test_existing_target_session_rejects_migration(
    tmp_path: Path,
) -> None:
    """目标已有 SQLite 数据时，不得重复迁移或创建新备份。"""
    legacy_file = tmp_path / "default.jsonl"
    legacy_file.write_text(
        '{"role": "user", "content": "旧问题"}\n',
        encoding="utf-8",
    )

    store = create_store(tmp_path)
    store.append_messages(
        "local:default",
        [{"role": "user", "content": "已有数据"}],
    )

    backup_directory = tmp_path / "legacy-backups"

    with pytest.raises(
        LegacyMigrationError,
        match="拒绝重复迁移",
    ):
        migrate_legacy_session(
            store,
            legacy_file,
            backup_directory,
        )

    assert not backup_directory.exists()


def test_same_content_reuses_existing_backup(
    tmp_path: Path,
) -> None:
    """相同旧文件重复备份时应复用同一份副本，不覆盖它。"""
    legacy_file = tmp_path / "default.jsonl"
    legacy_file.write_text(
        '{"role": "user", "content": "测试"}\n',
        encoding="utf-8",
    )

    backup_directory = tmp_path / "legacy-backups"

    first_backup = create_legacy_backup(
        legacy_file,
        backup_directory,
    )
    second_backup = create_legacy_backup(
        legacy_file,
        backup_directory,
    )

    assert first_backup == second_backup
    assert first_backup.exists()