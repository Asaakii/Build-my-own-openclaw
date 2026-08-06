import logging

from weather import WeatherError, get_current_weather

logger = logging.getLogger(__name__)

WEATHER_TOOL_DEFINITIONS: list[dict[str, object]] = [
    {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": (
                "根据城市名称查询当前模型天气。"
                "涉及实时天气时必须使用此工具，不能凭记忆回答。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "要查询天气的城市名称，例如：北京。",
                    },
                },
                "required": ["city"],
                "additionalProperties": False,
            },
        },
    },
]


def execute_weather_tool(
    tool_name: str,
    arguments: dict[str, object],
) -> str:
    """执行天气工具，并把网络失败转为可读结果。"""
    if tool_name != "get_current_weather":
        return f"工具执行失败：不支持的天气工具 {tool_name}"

    if set(arguments) != {"city"}:
        return "工具执行失败：get_current_weather 需要且只接受 city 参数"

    city = arguments["city"]
    if not isinstance(city, str):
        return "工具执行失败：city 必须是文本"

    try:
        report = get_current_weather(city)
    except WeatherError as error:
        logger.warning("天气工具查询失败")
        return f"工具执行失败：{error}"

    return report.to_text()