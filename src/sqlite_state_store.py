import json
import logging
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


logger = logging.getLogger(__name__)

# 数据库属于运行时状态，目录会被 .gitignore 忽略。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = PROJECT_ROOT / "data" / "myclaw.db"

# session_id 会出现在 API 路径和数据库主键中，因此限制格式，
# 不接受空白、斜杠或任意文件路径形式。
MAX_SESSION_ID_LENGTH = 120
SESSION_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9:_-]*$"
)

# 与旧 JSONL 存储保持相同的可持久化角色规则。
PERSISTED_ROLES = {"user", "assistant", "tool"}

# 当前任务表只建立数据结构；真正的持久化提醒逻辑留到 8.8。
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    message_index INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, message_index),
    FOREIGN KEY(session_id)
        REFERENCES sessions(session_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS messages_session_index
ON messages(session_id, message_index);

CREATE TABLE IF NOT EXISTS summaries (
    session_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(session_id)
        REFERENCES sessions(session_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    due_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(session_id)
        REFERENCES sessions(session_id)
        ON DELETE CASCADE
);
"""


class StateStoreError(RuntimeError):
    """表示 SQLite 状态层无法安全完成读写。"""


@dataclass(frozen=True)
class StoredSession:
    """保存从 SQLite 恢复的一份会话快照。"""

    messages: list[dict[str, object]]
    summary: str | None
    skipped_records: int


@dataclass(frozen=True)
class SessionInfo:
    """保存列表展示所需的会话元数据，不包含消息正文。"""

    session_id: str
    message_count: int
    has_summary: bool
    updated_at: str


def utc_now() -> str:
    """生成带时区的 UTC 时间，便于未来跨时区排序。"""
    return datetime.now(timezone.utc).isoformat()


def validate_session_id(session_id: str) -> str:
    """验证稳定会话标识，拒绝路径、空白和过长值。"""
    if (
        not isinstance(session_id, str)
        or not session_id
        or len(session_id) > MAX_SESSION_ID_LENGTH
        or not SESSION_ID_PATTERN.fullmatch(session_id)
    ):
        raise StateStoreError(
            "session_id 只能包含字母、数字、冒号、下划线和连字符"
        )

    return session_id


def is_valid_persisted_message(value: object) -> bool:
    """确认消息至少具备模型恢复所需的 role 和文本 content。"""
    if not isinstance(value, dict):
        return False

    return (
        value.get("role") in PERSISTED_ROLES
        and isinstance(value.get("content"), str)
    )


class SQLiteStateStore:
    """按 session_id 隔离保存会话、摘要和未来任务的 SQLite 状态层。"""

    def __init__(self, database_path: Path = DATABASE_PATH) -> None:
        self.database_path = database_path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """创建一次短生命周期连接，并保证提交、回滚和关闭。"""
        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        connection = sqlite3.connect(
            self.database_path,
            timeout=5,
        )
        connection.row_factory = sqlite3.Row

        # SQLite 默认不强制外键；每个连接都必须显式开启。
        connection.execute("PRAGMA foreign_keys = ON")

        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        """创建状态表；重复调用安全，不会清空已有数据。"""
        try:
            with self.connection() as connection:
                # WAL 为未来 Gateway 的读写并存做好准备。
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(SCHEMA)
        except (OSError, sqlite3.Error) as error:
            logger.error(
                "初始化 SQLite 状态层失败: error_type=%s",
                type(error).__name__,
            )
            raise StateStoreError(
                "无法初始化 SQLite 状态层"
            ) from error

    def touch_session(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        timestamp: str,
    ) -> None:
        """创建会话或更新时间；不覆盖最初创建时间。"""
        connection.execute(
            """
            INSERT INTO sessions (
                session_id,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                updated_at = excluded.updated_at
            """,
            (session_id, timestamp, timestamp),
        )

    def serialize_messages(
        self,
        messages: list[dict[str, object]],
    ) -> list[str]:
        """先完整校验和序列化，避免写入到一半才发现坏消息。"""
        serialized_messages: list[str] = []

        for message in messages:
            if not is_valid_persisted_message(message):
                raise StateStoreError("消息结构无效")

            try:
                serialized_messages.append(
                    json.dumps(
                        message,
                        ensure_ascii=False,
                    )
                )
            except TypeError as error:
                raise StateStoreError(
                    "消息无法序列化为 JSON"
                ) from error

        return serialized_messages

    def append_messages(
        self,
        session_id: str,
        messages: list[dict[str, object]],
    ) -> None:
        """向指定会话追加完整消息，不影响其他会话。"""
        normalized_session_id = validate_session_id(session_id)
        serialized_messages = self.serialize_messages(messages)

        if not serialized_messages:
            return

        self.initialize()
        timestamp = utc_now()

        try:
            with self.connection() as connection:
                self.touch_session(
                    connection,
                    normalized_session_id,
                    timestamp,
                )

                next_index_row = connection.execute(
                    """
                    SELECT COALESCE(
                        MAX(message_index),
                        -1
                    ) + 1 AS next_index
                    FROM messages
                    WHERE session_id = ?
                    """,
                    (normalized_session_id,),
                ).fetchone()

                next_index = int(
                    next_index_row["next_index"]
                )

                for offset, payload_json in enumerate(
                    serialized_messages
                ):
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
                            normalized_session_id,
                            next_index + offset,
                            payload_json,
                            timestamp,
                        ),
                    )
        except (OSError, sqlite3.Error) as error:
            logger.error(
                "追加 SQLite 会话消息失败: error_type=%s",
                type(error).__name__,
            )
            raise StateStoreError(
                "无法保存 SQLite 会话消息"
            ) from error

    def load_session(
        self,
        session_id: str,
    ) -> StoredSession:
        """读取指定会话；损坏的单条记录会被跳过。"""
        normalized_session_id = validate_session_id(session_id)
        self.initialize()

        messages: list[dict[str, object]] = []
        summary: str | None = None
        skipped_records = 0

        try:
            with self.connection() as connection:
                message_rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM messages
                    WHERE session_id = ?
                    ORDER BY message_index
                    """,
                    (normalized_session_id,),
                ).fetchall()

                for row in message_rows:
                    try:
                        message = json.loads(
                            row["payload_json"]
                        )
                    except json.JSONDecodeError:
                        skipped_records += 1
                        logger.warning(
                            "跳过损坏的 SQLite 消息记录"
                        )
                        continue

                    if not is_valid_persisted_message(message):
                        skipped_records += 1
                        logger.warning(
                            "跳过结构无效的 SQLite 消息记录"
                        )
                        continue

                    messages.append(message)

                summary_row = connection.execute(
                    """
                    SELECT content
                    FROM summaries
                    WHERE session_id = ?
                    """,
                    (normalized_session_id,),
                ).fetchone()

                if summary_row is not None:
                    candidate = summary_row["content"]

                    if (
                        isinstance(candidate, str)
                        and candidate.strip()
                    ):
                        summary = candidate
                    else:
                        skipped_records += 1
                        logger.warning(
                            "跳过结构无效的 SQLite 历史摘要"
                        )
        except (OSError, sqlite3.Error) as error:
            logger.error(
                "读取 SQLite 会话失败: error_type=%s",
                type(error).__name__,
            )
            raise StateStoreError(
                "无法读取 SQLite 会话"
            ) from error

        return StoredSession(
            messages=messages,
            summary=summary,
            skipped_records=skipped_records,
        )

    def replace_session_snapshot(
        self,
        session_id: str,
        messages: list[dict[str, object]],
        summary: str,
    ) -> None:
        """在一个事务中替换会话快照，供未来上下文压缩使用。"""
        normalized_session_id = validate_session_id(session_id)
        normalized_summary = summary.strip()

        if not normalized_summary:
            raise StateStoreError("历史摘要不能为空")

        serialized_messages = self.serialize_messages(messages)
        self.initialize()
        timestamp = utc_now()

        try:
            with self.connection() as connection:
                self.touch_session(
                    connection,
                    normalized_session_id,
                    timestamp,
                )

                # 删除和重建在同一事务内，失败时会整体回滚。
                connection.execute(
                    "DELETE FROM messages WHERE session_id = ?",
                    (normalized_session_id,),
                )
                connection.execute(
                    "DELETE FROM summaries WHERE session_id = ?",
                    (normalized_session_id,),
                )

                connection.execute(
                    """
                    INSERT INTO summaries (
                        session_id,
                        content,
                        updated_at
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        normalized_session_id,
                        normalized_summary,
                        timestamp,
                    ),
                )

                for message_index, payload_json in enumerate(
                    serialized_messages
                ):
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
                            normalized_session_id,
                            message_index,
                            payload_json,
                            timestamp,
                        ),
                    )
        except (OSError, sqlite3.Error) as error:
            logger.error(
                "替换 SQLite 会话快照失败: error_type=%s",
                type(error).__name__,
            )
            raise StateStoreError(
                "无法保存 SQLite 会话快照"
            ) from error

    def list_sessions(self) -> list[SessionInfo]:
        """返回会话元数据，不读取或暴露消息正文。"""
        self.initialize()

        try:
            with self.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT
                        sessions.session_id,
                        sessions.updated_at,
                        COUNT(messages.id) AS message_count,
                        CASE
                            WHEN summaries.session_id IS NULL THEN 0
                            ELSE 1
                        END AS has_summary
                    FROM sessions
                    LEFT JOIN messages
                        ON messages.session_id = sessions.session_id
                    LEFT JOIN summaries
                        ON summaries.session_id = sessions.session_id
                    GROUP BY
                        sessions.session_id,
                        sessions.updated_at,
                        summaries.session_id
                    ORDER BY sessions.updated_at DESC
                    """
                ).fetchall()
        except (OSError, sqlite3.Error) as error:
            logger.error(
                "列出 SQLite 会话失败: error_type=%s",
                type(error).__name__,
            )
            raise StateStoreError(
                "无法列出 SQLite 会话"
            ) from error

        return [
            SessionInfo(
                session_id=row["session_id"],
                message_count=int(row["message_count"]),
                has_summary=bool(row["has_summary"]),
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def session_exists(self, session_id: str) -> bool:
        """确认目标会话是否已经存在，迁移时用来拒绝重复导入。"""
        normalized_session_id = validate_session_id(session_id)
        self.initialize()

        try:
            with self.connection() as connection:
                row = connection.execute(
                    """
                    SELECT 1
                    FROM sessions
                    WHERE session_id = ?
                    """,
                    (normalized_session_id,),
                ).fetchone()
        except (OSError, sqlite3.Error) as error:
            logger.error(
                "检查 SQLite 会话是否存在失败: error_type=%s",
                type(error).__name__,
            )
            raise StateStoreError(
                "无法检查 SQLite 会话状态"
            ) from error

        return row is not None