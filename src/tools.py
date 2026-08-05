import logging
from datetime import datetime


logger = logging.getLogger(__name__)


# 这个列表发送给模型，用于告知允许使用哪些工具，参数长什么样
# 模型只能提出请求，不能自动执行函数
TOOL_DEFINITIONS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": (
                "获取运行 Agent 的电脑当前日期和时间"
                "当用户询问现在几点、今天日期或当前时间时必须使用此工具"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                # 此工具不接收任何参数，额外参数一律不允许
                "additionalProperties": False,
            },
        },
    },
]


def get_current_time() -> str:
    """返回运行 Agent 的电脑当前本地时间。"""
    # astimezone() 使用电脑当前时区，不依赖写死的城市或时区名称。
    now = datetime.now().astimezone()

    return now.strftime("%Y-%m-%d %H:%M:%S %Z")


# 只有这里登记过的 Python 函数，才可能被 Agent 调用。
# 不使用 eval、exec 或 shell 命令。
TOOL_FUNCTIONS = {
    "get_current_time": get_current_time,
}


def execute_tool(name: str, arguments: dict[str, object]) -> str:
    """根据工具名和参数调用对应的 Python 函数。"""
    if name not in TOOL_FUNCTIONS:
        # 即使模型幻觉出不存在的工具，程序也不会执行任何未知操作
        logger.warning("拒绝未知工具: tool_name=%s", name)
        return f"工具执行失败：不支持的工具 {name}"

    if arguments:
        # 当前唯一工具不接收参数，因此拒绝所有额外输入
        logger.warning("拒绝多余工具参数: tool_name=%s", name)
        return f"工具执行失败：工具 {name} 不接受任何参数"

    logger.info("执行工具: tool_name=%s", name)

    try:
        return TOOL_FUNCTIONS[name]()
    except Exception:
        # 不把底层异常、路径或其他内部信息直接暴露给模型
        logger.error("工具执行异常： tool_name=%s", name)
        return f"工具执行失败：{name} 执行时发生异常"