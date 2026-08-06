import logging

from skill_loader import SkillLoadError, load_skill

logger = logging.getLogger(__name__)

SKILL_TOOL_DEFINITIONS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": (
                "加载仓库 skills 目录中的任务技能说明。"
                "当用户明确要求使用某项技能、每日复盘或指定技能工作流时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "技能名称，例如：daily-review。",
                    },
                },
                "required": ["skill_name"],
                "additionalProperties": False,
            },
        },
    },
]


def execute_skill_tool(
    tool_name: str,
    arguments: dict[str, object],
) -> str:
    """执行只读技能加载工具。"""
    if tool_name != "load_skill":
        return f"工具执行失败：不支持的技能工具 {tool_name}"

    if set(arguments) != {"skill_name"}:
        return "工具执行失败：load_skill 需要且只接受 skill_name 参数"

    skill_name = arguments["skill_name"]
    if not isinstance(skill_name, str):
        return "工具执行失败：skill_name 必须是文本"

    try:
        return load_skill(skill_name)
    except SkillLoadError as error:
        logger.warning("技能加载被拒绝或失败")
        return f"工具执行失败：{error}"