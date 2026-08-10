"""Gateway 的最小代码级工具策略。

模型说明文字不是权限系统：即使模型错误或被提示注入诱导，
Gateway 仍必须在代码中决定哪些工具可以被展示和执行。
"""

# Gateway 只提供低风险读取、计算与明确授权的长期记忆能力。
# write_note 会改变本地文件，因此不在 Gateway 的最小策略中开放。
# save_memory 本身还会由 /remember 的逐字授权校验进一步限制。
GATEWAY_ALLOWED_TOOL_NAMES = frozenset(
    {
        "get_current_time",
        "calculate",
        "read_note",
        "search_memory",
        "save_memory",
        "get_current_weather",
        "load_skill",
    }
)
