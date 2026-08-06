import ast
import logging
import math
import operator
from datetime import datetime
from pathlib import Path

from memory_tools import MEMORY_TOOL_DEFINITIONS, execute_memory_tool
from weather_tools import WEATHER_TOOL_DEFINITIONS, execute_weather_tool


logger = logging.getLogger(__name__)

# 限制表达式长度和数值范围，避免恶意输入消耗大量资源
MAX_EXPRESSION_LENGTH = 100
MAX_ABSOLUTE_VALUE = 10**12
MAX_ABSOLUTE_EXPONENT = 20

# 限制笔记名称和内容，避免工具被用来写入过大的数据。
MAX_NOTE_NAME_LENGTH = 80
MAX_NOTE_CONTENT_LENGTH = 4_000

# tools.py 位于 src/ 中，因此上两级目录就是项目根目录。
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 所有笔记都只能放在这个目录中。
NOTES_DIRECTORY = (PROJECT_ROOT / "workspace" / "notes").resolve()

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
    {
        "type": "function",
        "function": {
            "name": "read_note",
            "description": "读取 workspace/notes 目录中的一份 Markdown 笔记。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "笔记文件名，例如：study-note.md",
                    },
                },
                "required": ["filename"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_note",
            "description": (
                "在 workspace/notes 目录中新建一份 Markdown 笔记。"
                "同名笔记不会被覆盖。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "新笔记文件名，例如：study-note.md",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入笔记的正文内容。",
                    },
                },
                "required": ["filename", "content"],
                "additionalProperties": False,
            },
        },
    },
]

# 长期记忆工具独立定义，再加入统一的模型工具清单。
TOOL_DEFINITIONS.extend(MEMORY_TOOL_DEFINITIONS)

# 天气工具独立定义，再加入统一的模型工具清单。
TOOL_DEFINITIONS.extend(WEATHER_TOOL_DEFINITIONS)


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


def get_note_path(filename: str) -> Path:
    """验证文件名，并返回确定处于笔记目录内的绝对路径。"""
    if not filename or len(filename) > MAX_NOTE_NAME_LENGTH:
        raise ValueError("笔记文件名为空或过长")

    candidate = Path(filename)

    # 文件名必须是单个 .md 名称，不能含 ../、绝对路径或隐藏文件名。
    if (
        candidate.is_absolute()
        or len(candidate.parts) != 1
        or candidate.name != filename
        or "/" in filename
        or "\\" in filename
        or filename.startswith(".")
        or not filename.endswith(".md")
    ):
        raise ValueError("笔记文件名必须是单个 .md 文件名，不能包含路径")

    # resolve() 会处理符号链接；relative_to() 是真正的目录边界检查。
    note_path = (NOTES_DIRECTORY / filename).resolve()

    try:
        note_path.relative_to(NOTES_DIRECTORY)
    except ValueError as error:
        raise ValueError("笔记路径超出允许目录") from error

    return note_path


def read_note(filename: str) -> str:
    """读取一份受限目录中的 UTF-8 Markdown 笔记。"""
    note_path = get_note_path(filename)

    if not note_path.exists():
        raise ValueError("笔记不存在")

    if not note_path.is_file():
        raise ValueError("笔记路径不是普通文件")

    # UTF-8 的一个字符最多占 4 个字节，先限制文件体积，
    # 避免超大文件在读取时一次性占用大量内存。
    if note_path.stat().st_size > MAX_NOTE_CONTENT_LENGTH * 4:
        raise ValueError("笔记内容超过读取上限")

    content = note_path.read_text(encoding="utf-8")

    # 再按字符数检查，保证模型收到的文本也不会过长。
    if len(content) > MAX_NOTE_CONTENT_LENGTH:
        raise ValueError("笔记内容超过读取上限")

    return content


def write_note(filename: str, content: str) -> str:
    """创建新笔记；同名文件一律拒绝覆盖。"""
    if not content.strip():
        raise ValueError("笔记内容不能为空")

    if len(content) > MAX_NOTE_CONTENT_LENGTH:
        raise ValueError("笔记内容超过写入上限")

    note_path = get_note_path(filename)
    NOTES_DIRECTORY.mkdir(parents=True, exist_ok=True)

    try:
        # x 模式只允许创建新文件，文件已存在时会抛出 FileExistsError。
        with note_path.open("x", encoding="utf-8") as note_file:
            note_file.write(content)
    except FileExistsError as error:
        raise ValueError("同名笔记已经存在，首版不允许覆写") from error

    return f"笔记已创建：{filename}"


def execute_tool(
    tool_name: str,
    arguments: dict[str, object],
    authorized_memory_content: str | None = None,
) -> str:
    """执行白名单工具，并在执行前再次校验参数。"""
    logger.info("开始执行工具: tool_name=%s", tool_name)

    if tool_name in {"save_memory", "search_memory"}:
        return execute_memory_tool(
            tool_name,
            arguments,
            authorized_memory_content,
        )

    if tool_name == "get_current_weather":
        return execute_weather_tool(tool_name, arguments)

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

    if tool_name == "read_note":
        if set(arguments) != {"filename"}:
            return "工具执行失败：read_note 需要且只接受 filename 参数"

        filename = arguments["filename"]
        if not isinstance(filename, str):
            return "工具执行失败：filename 必须是文本"

        try:
            return read_note(filename)
        except ValueError as error:
            logger.warning("读取笔记被拒绝或失败")
            return f"工具执行失败：{error}"
        except OSError:
            logger.warning("读取笔记时发生系统错误")
            return "工具执行失败：读取笔记时发生异常"

    if tool_name == "write_note":
        if set(arguments) != {"filename", "content"}:
            return "工具执行失败：write_note 需要 filename 和 content 参数"

        filename = arguments["filename"]
        content = arguments["content"]

        if not isinstance(filename, str) or not isinstance(content, str):
            return "工具执行失败：filename 和 content 必须是文本"

        try:
            return write_note(filename, content)
        except ValueError as error:
            logger.warning("写入笔记被拒绝或失败")
            return f"工具执行失败：{error}"
        except OSError:
            logger.warning("写入笔记时发生系统错误")
            return "工具执行失败：写入笔记时发生异常"

    logger.warning("拒绝未知工具: tool_name=%s", tool_name)
    return f"工具执行失败：不支持的工具 {tool_name}"