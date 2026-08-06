import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# session_store.py 位于 src/ 中，因此上两级目录是项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 首版只维护一个默认会话文件；多会话管理留到后续再设计。
SESSION_FILE = PROJECT_ROOT / "sessions" / "default.jsonl"
TEMP_SESSION_FILE = SESSION_FILE.with_name(f"{SESSION_FILE.name}.tmp")

# system 消息每次启动都从最新 SOUL.md 读取，所以不保存到会话文件。
PERSISTED_ROLES = {"user", "assistant", "tool"}
SUMMARY_RECORD_TYPE = "context_summary"


class SessionStoreError(RuntimeError):
    """表示本地会话读取或保存失败。"""


@dataclass
class SessionLoadResult:
    """保存加载结果，方便区分有效消息和被跳过的损坏记录。"""

    messages: list[dict[str, object]]
    summary: str | None
    skipped_lines: int


def is_valid_persisted_message(value: object) -> bool:
    """检查 JSON 解析结果是否至少具备可恢复的消息基本结构。"""
    if not isinstance(value, dict):
        return False

    return (
        value.get("role") in PERSISTED_ROLES
        and isinstance(value.get("content"), str)
    )


def is_valid_summary_record(value: object) -> bool:
    """检查 JSONL 中的历史摘要元数据。"""
    if not isinstance(value, dict):
        return False

    return (
        value.get("record_type") == SUMMARY_RECORD_TYPE
        and isinstance(value.get("content"), str)
        and bool(value["content"].strip())
    )


def load_session_messages() -> SessionLoadResult:
    """读取 JSONL 会话文件，跳过损坏行并保留其他有效记录。"""
    if not SESSION_FILE.exists():
        return SessionLoadResult(
            messages=[],
            summary=None,
            skipped_lines=0,
        )

    messages: list[dict[str, object]] = []
    summary: str | None = None
    skipped_lines = 0

    try:
        with SESSION_FILE.open("r", encoding="utf-8") as session_file:
            for line_number, line in enumerate(session_file, start=1):
                # 空行没有信息，直接忽略，不视为损坏记录。
                if not line.strip():
                    continue

                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    skipped_lines += 1
                    logger.warning("跳过损坏的会话记录: line_number=%d", line_number)
                    continue

                # 摘要是元数据，不会作为普通消息恢复。
                if is_valid_summary_record(value):
                    summary = value["content"]
                    continue

                if not is_valid_persisted_message(value):
                    skipped_lines += 1
                    logger.warning("跳过结构无效的会话记录: line_number=%d", line_number)
                    continue

                messages.append(value)

    except OSError as error:
        logger.error("读取会话文件失败: error_type=%s", type(error).__name__)
        raise SessionStoreError("无法读取本地会话记录") from error

    return SessionLoadResult(
        messages=messages,
        summary=summary,
        skipped_lines=skipped_lines,
    )


def append_session_messages(messages: list[dict[str, object]]) -> None:
    """把一轮已完成的消息追加为多行 JSONL，不覆盖既有记录。"""
    if not messages:
        return

    try:
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)

        with SESSION_FILE.open("a", encoding="utf-8") as session_file:
            for message in messages:
                # ensure_ascii=False 保留中文，便于本机排查；文件本身不会提交 Git。
                serialized_message = json.dumps(message, ensure_ascii=False)
                session_file.write(f"{serialized_message}\n")

    except (OSError, TypeError) as error:
        logger.error("保存会话文件失败: error_type=%s", type(error).__name__)
        raise SessionStoreError("无法保存本地会话记录") from error


def replace_session_snapshot(
    messages: list[dict[str, object]],
    summary: str,
) -> None:
    """原子替换会话快照，保存新摘要和压缩后保留的消息。"""
    try:
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)

        with TEMP_SESSION_FILE.open("w", encoding="utf-8") as session_file:
            summary_record = {
                "record_type": SUMMARY_RECORD_TYPE,
                "content": summary,
            }
            session_file.write(
                f"{json.dumps(summary_record, ensure_ascii=False)}\n"
            )

            for message in messages:
                if not is_valid_persisted_message(message):
                    raise SessionStoreError("会话快照包含无效消息")

                session_file.write(
                    f"{json.dumps(message, ensure_ascii=False)}\n"
                )

        # 临时文件与目标文件在同一目录，replace() 可避免半截覆盖。
        TEMP_SESSION_FILE.replace(SESSION_FILE)

    except (OSError, TypeError) as error:
        logger.error("替换会话快照失败: error_type=%s", type(error).__name__)
        raise SessionStoreError("无法保存压缩后的会话快照") from error