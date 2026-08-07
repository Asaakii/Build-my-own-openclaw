import hashlib
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from session_store import (
    SessionStoreError,
    load_session_messages,
)
from sqlite_state_store import (
    SQLiteStateStore,
    StateStoreError,
    validate_session_id,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 旧版终端 Agent 使用的默认 JSONL 会话记录。
LEGACY_SESSION_FILE = PROJECT_ROOT / "sessions" / "default.jsonl"

# 备份与旧会话同属运行时数据目录，不会提交到 Git。
LEGACY_BACKUP_DIRECTORY = PROJECT_ROOT / "sessions" / "legacy-backups"

# 新状态层中为旧终端会话保留的稳定标识。
DEFAULT_LEGACY_SESSION_ID = "local:default"


class LegacyMigrationError(RuntimeError):
    """表示旧会话无法安全迁移到 SQLite。"""


@dataclass(frozen=True)
class LegacyMigrationResult:
    """保存迁移结果，但不包含任何聊天正文。"""

    backup_path: Path
    messages_imported: int
    summary_imported: bool
    skipped_lines: int


def create_legacy_backup(
    legacy_session_file: Path,
    backup_directory: Path,
) -> Path:
    """创建按内容哈希命名的备份，重复执行不会覆盖已有备份。"""
    if not legacy_session_file.is_file():
        raise LegacyMigrationError("旧会话文件不存在，无法迁移")

    try:
        source_bytes = legacy_session_file.read_bytes()
    except OSError as error:
        logger.error(
            "读取旧会话备份源失败: error_type=%s",
            type(error).__name__,
        )
        raise LegacyMigrationError("无法读取旧会话文件") from error

    # 相同内容总会得到同一个备份名称；不同内容则保留为另一份备份。
    content_hash = hashlib.sha256(source_bytes).hexdigest()[:16]
    backup_name = (
        f"{legacy_session_file.stem}-"
        f"{content_hash}"
        f"{legacy_session_file.suffix}.bak"
    )
    backup_path = backup_directory / backup_name

    try:
        backup_directory.mkdir(parents=True, exist_ok=True)

        if backup_path.exists():
            # 已有同名备份时只允许内容完全一致，绝不静默覆盖。
            if backup_path.read_bytes() != source_bytes:
                raise LegacyMigrationError("检测到不一致的旧会话备份")

            return backup_path

        shutil.copy2(legacy_session_file, backup_path)

        # 导入前再次核对，确保备份副本完整。
        if backup_path.read_bytes() != source_bytes:
            raise LegacyMigrationError("旧会话备份校验失败")

    except OSError as error:
        logger.error(
            "创建旧会话备份失败: error_type=%s",
            type(error).__name__,
        )
        raise LegacyMigrationError("无法创建旧会话备份") from error

    return backup_path


def migrate_legacy_session(
    state_store: SQLiteStateStore,
    legacy_session_file: Path = LEGACY_SESSION_FILE,
    backup_directory: Path = LEGACY_BACKUP_DIRECTORY,
    session_id: str = DEFAULT_LEGACY_SESSION_ID,
) -> LegacyMigrationResult:
    """备份旧 JSONL 后导入 SQLite；目标会话已存在时拒绝重复迁移。"""
    normalized_session_id = validate_session_id(session_id)

    # 先检查目标，防止已有 SQLite 数据被覆盖或重复追加。
    if state_store.session_exists(normalized_session_id):
        raise LegacyMigrationError("目标 SQLite 会话已存在，拒绝重复迁移")

    # 先备份，后续只从备份读取，保证导入依据可追溯。
    backup_path = create_legacy_backup(
        legacy_session_file,
        backup_directory,
    )

    try:
        loaded_session = load_session_messages(backup_path)

        if not loaded_session.messages and loaded_session.summary is None:
            raise LegacyMigrationError("旧会话中没有可迁移的有效记录")

        if loaded_session.summary is not None:
            # 带摘要时用快照写入，以保持压缩后的会话结构。
            state_store.replace_session_snapshot(
                normalized_session_id,
                loaded_session.messages,
                loaded_session.summary,
            )
        else:
            state_store.append_messages(
                normalized_session_id,
                loaded_session.messages,
            )
    except (SessionStoreError, StateStoreError) as error:
        logger.error(
            "迁移旧会话失败: error_type=%s",
            type(error).__name__,
        )
        raise LegacyMigrationError("无法导入旧会话记录") from error

    return LegacyMigrationResult(
        backup_path=backup_path,
        messages_imported=len(loaded_session.messages),
        summary_imported=loaded_session.summary is not None,
        skipped_lines=loaded_session.skipped_lines,
    )