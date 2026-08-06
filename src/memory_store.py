import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# memory_store.py 位于 src/ 中，因此上两级目录是项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 长期记忆只保存在这一个本地 Markdown 文件中。
MEMORY_FILE = PROJECT_ROOT / "workspace" / "MEMORY.md"

MAX_MEMORY_ENTRY_LENGTH = 300
MAX_MEMORY_FILE_BYTES = 20_000


class MemoryStoreError(RuntimeError):
    """表示长期记忆文件无法安全读取或写入。"""


def normalize_memory_text(text: str) -> str:
    """去除首尾空白并合并换行，保证一条记忆只占 Markdown 的一行。"""
    normalized_text = " ".join(text.split())

    if not normalized_text:
        raise ValueError("长期记忆内容不能为空")

    if len(normalized_text) > MAX_MEMORY_ENTRY_LENGTH:
        raise ValueError("长期记忆内容超过长度上限")

    return normalized_text


def load_memory_entries() -> list[str]:
    """读取 Markdown 中以 '- ' 开头的长期记忆条目。"""
    if not MEMORY_FILE.exists():
        return []

    try:
        if MEMORY_FILE.stat().st_size > MAX_MEMORY_FILE_BYTES:
            raise MemoryStoreError("长期记忆文件超过读取上限")

        content = MEMORY_FILE.read_text(encoding="utf-8")
    except OSError as error:
        logger.error("读取长期记忆失败: error_type=%s", type(error).__name__)
        raise MemoryStoreError("无法读取长期记忆") from error

    entries: list[str] = []

    for line in content.splitlines():
        if line.startswith("- "):
            entry = line[2:].strip()

            if entry:
                entries.append(entry)

    return entries


def save_memory(content: str) -> str:
    """将一条新记忆追加到 Markdown 文件，重复内容不重复保存。"""
    normalized_content = normalize_memory_text(content)
    entries = load_memory_entries()

    if normalized_content in entries:
        return "该长期记忆已经存在"

    try:
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)

        if not MEMORY_FILE.exists():
            MEMORY_FILE.write_text(
                "# 长期记忆\n\n",
                encoding="utf-8",
            )

        if MEMORY_FILE.stat().st_size > MAX_MEMORY_FILE_BYTES:
            raise MemoryStoreError("长期记忆文件超过写入上限")

        with MEMORY_FILE.open("a", encoding="utf-8") as memory_file:
            memory_file.write(f"- {normalized_content}\n")

    except OSError as error:
        logger.error("写入长期记忆失败: error_type=%s", type(error).__name__)
        raise MemoryStoreError("无法保存长期记忆") from error

    return "长期记忆已保存"


def search_memories(query: str) -> list[str]:
    """按关键词进行大小写不敏感的简单检索。"""
    normalized_query = normalize_memory_text(query)
    matches: list[str] = []

    for entry in load_memory_entries():
        if normalized_query.casefold() in entry.casefold():
            matches.append(entry)

    return matches