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

CREATE INDEX IF NOT EXISTS tasks_status_due_at
ON tasks(status, due_at);
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


@dataclass(frozen=True)
class StoredTask:
    """保存 Gateway 调度所需的任务数据，包含内部 payload。"""

    task_id: str
    session_id: str
    task_type: str
    status: str
    payload: dict[str, object]
    due_at: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TaskInfo:
    """保存可安全展示的任务元数据，不包含提醒正文或投递地址。"""

    task_id: str
    session_id: str
    task_type: str
    status: str
    due_at: str
    updated_at: str


TASK_STATUSES = {
    "pending",
    "delivering",
    "delivered",
    "failed",
}


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

    def create_task(
        self,
        task_id: str,
        session_id: str,
        task_type: str,
        payload: dict[str, object],
        due_at: str,
    ) -> TaskInfo:
        """原子保存待执行任务，并为任务所属会话建立元数据。"""
        normalized_session_id = validate_session_id(session_id)

        if (
            not isinstance(task_id, str)
            or not task_id
            or not isinstance(task_type, str)
            or not task_type
            or not isinstance(payload, dict)
            or not isinstance(due_at, str)
            or not due_at.strip()
        ):
            raise StateStoreError("任务数据无效")

        try:
            payload_json = json.dumps(
                payload,
                ensure_ascii=False,
            )
        except TypeError as error:
            raise StateStoreError("任务数据无法序列化") from error

        self.initialize()
        timestamp = utc_now()

        try:
            with self.connection() as connection:
                self.touch_session(
                    connection,
                    normalized_session_id,
                    timestamp,
                )
                connection.execute(
                    """
                    INSERT INTO tasks (
                        task_id,
                        session_id,
                        task_type,
                        status,
                        payload_json,
                        due_at,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        normalized_session_id,
                        task_type,
                        "pending",
                        payload_json,
                        due_at,
                        timestamp,
                        timestamp,
                    ),
                )
        except (OSError, sqlite3.Error) as error:
            logger.error(
                "创建 SQLite 任务失败: error_type=%s",
                type(error).__name__,
            )
            raise StateStoreError("无法创建任务") from error

        return TaskInfo(
            task_id=task_id,
            session_id=normalized_session_id,
            task_type=task_type,
            status="pending",
            due_at=due_at,
            updated_at=timestamp,
        )

    def decode_task_row(
        self,
        row: sqlite3.Row,
    ) -> StoredTask | None:
        """解析任务 payload；损坏任务不交给调度器执行。"""
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            logger.warning("跳过损坏的 SQLite 任务记录")
            return None

        if not isinstance(payload, dict):
            logger.warning("跳过结构无效的 SQLite 任务记录")
            return None

        due_at = row["due_at"]

        if not isinstance(due_at, str) or not due_at:
            logger.warning("跳过缺少到期时间的 SQLite 任务记录")
            return None

        return StoredTask(
            task_id=row["task_id"],
            session_id=row["session_id"],
            task_type=row["task_type"],
            status=row["status"],
            payload=payload,
            due_at=due_at,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_tasks(self) -> list[TaskInfo]:
        """返回任务元数据，不读取或暴露提醒正文和投递地址。"""
        self.initialize()

        try:
            with self.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT
                        task_id,
                        session_id,
                        task_type,
                        status,
                        due_at,
                        updated_at
                    FROM tasks
                    ORDER BY due_at ASC, created_at ASC
                    """
                ).fetchall()
        except (OSError, sqlite3.Error) as error:
            logger.error(
                "列出 SQLite 任务失败: error_type=%s",
                type(error).__name__,
            )
            raise StateStoreError("无法列出任务") from error

        return [
            TaskInfo(
                task_id=row["task_id"],
                session_id=row["session_id"],
                task_type=row["task_type"],
                status=row["status"],
                due_at=row["due_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def count_tasks_with_status(
        self,
        statuses: set[str],
    ) -> int:
        """统计未终结任务，用于限制同时等待或发送的提醒数量。"""
        if not statuses or not statuses.issubset(TASK_STATUSES):
            raise StateStoreError("任务状态无效")

        self.initialize()
        placeholders = ", ".join("?" for _ in statuses)

        try:
            with self.connection() as connection:
                row = connection.execute(
                    f"""
                    SELECT COUNT(*) AS task_count
                    FROM tasks
                    WHERE status IN ({placeholders})
                    """,
                    tuple(sorted(statuses)),
                ).fetchone()
        except (OSError, sqlite3.Error) as error:
            logger.error(
                "统计 SQLite 任务失败: error_type=%s",
                type(error).__name__,
            )
            raise StateStoreError("无法统计任务") from error

        return int(row["task_count"])

    def claim_due_tasks(
        self,
        task_type: str,
        due_before: str,
        limit: int,
    ) -> list[StoredTask]:
        """将到期任务原子改为发送中，避免重复调度同一任务。"""
        if (
            not isinstance(task_type, str)
            or not task_type
            or not isinstance(due_before, str)
            or not due_before
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise StateStoreError("领取任务参数无效")

        self.initialize()
        timestamp = utc_now()
        claimed_tasks: list[StoredTask] = []

        try:
            with self.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM tasks
                    WHERE task_type = ?
                        AND status = 'pending'
                        AND due_at <= ?
                    ORDER BY due_at ASC, created_at ASC
                    LIMIT ?
                    """,
                    (task_type, due_before, limit),
                ).fetchall()

                for row in rows:
                    updated = connection.execute(
                        """
                        UPDATE tasks
                        SET status = 'delivering', updated_at = ?
                        WHERE task_id = ? AND status = 'pending'
                        """,
                        (timestamp, row["task_id"]),
                    )

                    if updated.rowcount != 1:
                        continue

                    task = self.decode_task_row(row)

                    if task is None:
                        connection.execute(
                            """
                            UPDATE tasks
                            SET status = 'failed', updated_at = ?
                            WHERE task_id = ?
                            """,
                            (timestamp, row["task_id"]),
                        )
                        continue

                    claimed_tasks.append(
                        StoredTask(
                            task_id=task.task_id,
                            session_id=task.session_id,
                            task_type=task.task_type,
                            status="delivering",
                            payload=task.payload,
                            due_at=task.due_at,
                            created_at=task.created_at,
                            updated_at=timestamp,
                        )
                    )
        except (OSError, sqlite3.Error) as error:
            logger.error(
                "领取 SQLite 到期任务失败: error_type=%s",
                type(error).__name__,
            )
            raise StateStoreError("无法领取到期任务") from error

        return claimed_tasks

    def complete_claimed_task(
        self,
        task_id: str,
        status: str,
    ) -> None:
        """结束发送中的任务，拒绝跳过状态机直接更新。"""
        if status not in {"delivered", "failed"}:
            raise StateStoreError("任务终结状态无效")

        self.initialize()

        try:
            with self.connection() as connection:
                updated = connection.execute(
                    """
                    UPDATE tasks
                    SET status = ?, updated_at = ?
                    WHERE task_id = ? AND status = 'delivering'
                    """,
                    (status, utc_now(), task_id),
                )
        except (OSError, sqlite3.Error) as error:
            logger.error(
                "更新 SQLite 任务状态失败: error_type=%s",
                type(error).__name__,
            )
            raise StateStoreError("无法更新任务状态") from error

        if updated.rowcount != 1:
            raise StateStoreError("任务状态已变化，拒绝重复更新")

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
