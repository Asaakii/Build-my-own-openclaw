import ast
import logging
import math
import operator
from datetime import datetime


logger = logging.getLogger(__name__)

# 限制表达式长度和数值范围，避免恶意输入消耗大量资源
MAX_EXPRESSION_LENGTH = 100
MAX_ABSOLUTE_VALUE = 10**12
MAX_ABSOLUTE_EXPONENT = 20

# 只允许这些二元运算符。
BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

# 只允许正号和负号这样的单目运算符。
UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# 这是提供给模型的工具说明，模型只能申请使用，程序仍会自行校验
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
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": (
                "计算基础算术表达式。支持数字、括号和 "
                "+、-、*、/、//、%、** 运算符，不能执行代码。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "需要计算的算术表达式，例如：(23 * 17 + 6) / 5",
                    },
                },
                "required": ["expression"],
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


def ensure_safe_number(value: int | float) -> int | float:
    """确认中间结果仍是范围可控的普通数字。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("只允许普通数字")

    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("计算结果不是有限数字")

    if abs(value) > MAX_ABSOLUTE_VALUE:
        raise ValueError("计算结果超出允许范围")

    return value


def evaluate_node(node: ast.AST) -> int | float:
    """递归计算语法树中被允许的节点，其他节点一律拒绝。"""
    if isinstance(node, ast.Constant):
        return ensure_safe_number(node.value)

    if isinstance(node, ast.UnaryOp) and type(node.op) in UNARY_OPERATORS:
        operand = evaluate_node(node.operand)
        return ensure_safe_number(UNARY_OPERATORS[type(node.op)](operand))

    if isinstance(node, ast.BinOp) and type(node.op) in BINARY_OPERATORS:
        left = evaluate_node(node.left)
        right = evaluate_node(node.right)

        # 幂运算特别容易产生巨大数字，因此额外限制指数。
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_ABSOLUTE_EXPONENT:
            raise ValueError("幂运算的指数超出允许范围")

        try:
            result = BINARY_OPERATORS[type(node.op)](left, right)
        except ZeroDivisionError as error:
            raise ValueError("不能除以零") from error

        return ensure_safe_number(result)

    # 函数调用、变量、属性访问、列表等都会进入这里并被拒绝。
    raise ValueError("只允许数字、括号和基础算术运算符")


def calculate(expression: str) -> str:
    """解析并计算受限的算术表达式，绝不执行输入中的 Python 代码。"""
    expression = expression.strip()

    if not expression:
        raise ValueError("表达式不能为空")

    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ValueError("表达式过长")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise ValueError("表达式语法无效") from error

    result = evaluate_node(tree.body)
    return str(result)


def execute_tool(tool_name: str, arguments: dict[str, object]) -> str:
    """执行白名单工具，并在执行前再次校验参数。"""
    logger.info("开始执行工具: tool_name=%s", tool_name)

    if tool_name == "get_current_time":
        if arguments:
            return "工具执行失败：get_current_time 不接受参数"

        return get_current_time()

    if tool_name == "calculate":
        if set(arguments) != {"expression"}:
            return "工具执行失败：calculate 需要且只接受 expression 参数"

        expression = arguments["expression"]
        if not isinstance(expression, str):
            return "工具执行失败：expression 必须是文本"

        try:
            return calculate(expression)
        except ValueError as error:
            logger.warning("计算工具拒绝了无效表达式")
            return f"工具执行失败：{error}"

    logger.warning("拒绝未知工具: tool_name=%s", tool_name)
    return f"工具执行失败：不支持的工具 {tool_name}"