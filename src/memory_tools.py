import logging

from memory_store import (
    MemoryStoreError,
    normalize_memory_text,
    save_memory,
    search_memories,
)

logger = logging.getLogger(__name__)

MEMORY_TOOL_DEFINITIONS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": (
                "保存一条用户已通过 /remember 明确授权的长期记忆。"
                "不得用于普通聊天内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "需要保存的长期记忆内容。",
                    },
                },
                "required": ["content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "按关键词检索用户已经明确保存的长期记忆。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "用于检索长期记忆的关键词。",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
]


def execute_memory_tool(
    tool_name: str,
    arguments: dict[str, object],
    authorized_memory_content: str | None,
) -> str:
    """执行长期记忆工具，并校验保存内容是否得到本轮明确授权。"""
    if tool_name == "save_memory":
        if set(arguments) != {"content"}:
            return "工具执行失败：save_memory 需要且只接受 content 参数"

        content = arguments["content"]
        if not isinstance(content, str):
            return "工具执行失败：content 必须是文本"

        # 没有 /remember 授权时，即使模型请求保存也必须拒绝。
        if authorized_memory_content is None:
            logger.warning("拒绝未经授权的长期记忆保存")
            return "工具执行失败：保存长期记忆需要使用 /remember 明确授权"

        try:
            normalized_content = normalize_memory_text(content)
            normalized_authorization = normalize_memory_text(
                authorized_memory_content
            )
        except ValueError as error:
            return f"工具执行失败：{error}"

        # 防止模型把用户授权的一句话替换为其他内容进行保存。
        if normalized_content != normalized_authorization:
            logger.warning("拒绝与授权内容不一致的长期记忆保存")
            return "工具执行失败：保存内容与 /remember 授权内容不一致"

        try:
            return save_memory(normalized_content)
        except (ValueError, MemoryStoreError) as error:
            logger.warning("保存长期记忆失败")
            return f"工具执行失败：{error}"

    if tool_name == "search_memory":
        if set(arguments) != {"query"}:
            return "工具执行失败：search_memory 需要且只接受 query 参数"

        query = arguments["query"]
        if not isinstance(query, str):
            return "工具执行失败：query 必须是文本"

        try:
            matches = search_memories(query)
        except (ValueError, MemoryStoreError) as error:
            logger.warning("检索长期记忆失败")
            return f"工具执行失败：{error}"

        if not matches:
            return "没有找到匹配的长期记忆"

        return "找到的长期记忆：\n" + "\n".join(
            f"- {match}" for match in matches
        )

    return f"工具执行失败：不支持的记忆工具 {tool_name}"