import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# session_store.py 位于 src/ 中，因此上两级目录是项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 首版只维护一个默认会话文件；多会话管理留到后续再设计。
SESSION_FILE = PROJECT_ROOT / "sessions" / "default.jsonl"

# system 消息每次启动都从最新 SOUL.md 读取，所以不保存到会话文件。
PERSISTED_ROLES = {"user", "assistant", "tool"}


class SessionStoreError(RuntimeError):
    """表示本地会话读取或保存失败。"""


@dataclass
class SessionLoadResult:
    """保存加载结果，方便区分有效消息和被跳过的损坏记录。"""

    messages: list[dict[str, object]]
    skipped_lines: int


def is_valid_persisted_message(value: object) -> bool:
    """检查 JSON 解析结果是否至少具备可恢复的消息基本结构。"""
    if not isinstance(value, dict):
        return False

    role = value.get("role")
    content = value.get("content")

    return role in PERSISTED_ROLES and isinstance(content, str)


def load_session_messages() -> SessionLoadResult:
    """读取 JSONL 会话文件，跳过损坏行并保留其他有效记录。"""
    if not SESSION_FILE.exists():
        return SessionLoadResult(messages=[], skipped_lines=0)

    messages: list[dict[str, object]] = []
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

                if not is_valid_persisted_message(value):
                    skipped_lines += 1
                    logger.warning("跳过结构无效的会话记录: line_number=%d", line_number)
                    continue

                messages.append(value)

    except OSError as error:
        logger.error("读取会话文件失败: error_type=%s", type(error).__name__)
        raise SessionStoreError("无法读取本地会话记录") from error

    return SessionLoadResult(messages=messages, skipped_lines=skipped_lines)


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