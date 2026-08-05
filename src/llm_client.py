import json
import logging

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from config import ModelConfig, load_model_config
from tools import TOOL_DEFINITIONS, execute_tool


# 这里固定使用 DeepSeek 作为供应商，后续可以扩展为支持其他供应商
# 使用 openai SDk 是因为 DeepSeek 兼容 OpenAI API，方便统一调用
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# 单次用户消息最多真正执行多少次工具
# 这是一条代码层面的安全限制，不能只依赖提示词
MAX_TOOL_CALLS = 3

logger = logging.getLogger(__name__)


class LLMClientError(RuntimeError):
    """表示模型服务调用失败，但不暴露敏感信息。"""
    


def create_deepseek_client(config: ModelConfig) -> OpenAI:
    """根据已验证的配置创建 DeepSeek 客户端。"""
    if config.provider != "deepseek":
        raise ValueError(
            f"当前只支持 deepseek，实际配置为: {config.provider}"
        )

    # API key 只传给 SDK，不在日志中打印，避免泄露
    return OpenAI(
        api_key=config.api_key,
        base_url=DEEPSEEK_BASE_URL,
        # 使用 .env 中的超时值，方便控制与测试
        timeout=config.timeout_seconds,
        # 首版不自动重试，避免失败原因和等待时间不透明
        # 后续会在可靠性阶段设计更完整的重试策略
        max_retries=0,
    )

def request_completion(
    client: OpenAI,
    config: ModelConfig,
    messages: list[dict[str, object]],
):
    """向模型发送一次请求，并允许模型从白名单中选择工具。"""
    logger.info("开始模型请求: history_messages=%d", len(messages))

    try:
        response = client.chat.completions.create(
            model=config.model,
            messages=messages,
            tools=TOOL_DEFINITIONS,
            max_tokens=512,
            # 工具调用首版关闭思考模式，避免处理 reasoning_content。
            extra_body={
                "thinking": {
                    "type": "disabled",
                }
            },
        )
    except APITimeoutError as error:
        logger.warning(
            "模型请求超时: timeout_seconds=%s",
            config.timeout_seconds,
        )
        raise LLMClientError("模型请求超时，请稍后再试") from error
    except AuthenticationError as error:
        logger.warning("模型认证失败")
        raise LLMClientError("API Key 无效或已失效") from error
    except RateLimitError as error:
        logger.warning("模型请求触发限流")
        raise LLMClientError("请求过于频繁，请稍后再试") from error
    except APIConnectionError as error:
        logger.warning("无法连接模型服务")
        raise LLMClientError("无法连接到模型服务，请检查网络") from error
    except APIStatusError as error:
        logger.warning(
            "模型服务返回错误状态码: status_code=%s",
            error.status_code,
        )
        raise LLMClientError(
            f"模型服务返回错误状态码，状态码: {error.status_code}"
        ) from error

    return response.choices[0].message


def serialize_assistant_tool_message(message) -> dict[str, object]:
    """将模型的工具请求转换为下一轮 API 可接收的消息字典。"""
    tool_calls = []

    for tool_call in message.tool_calls or []:
        tool_calls.append(
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments,
                },
            }
        )

    return {
        "role": "assistant",
        # 工具调用时模型可能暂时没有自然语言内容。
        "content": message.content or "",
        "tool_calls": tool_calls,
    }


def run_agent_turn(messages: list[dict[str, object]]) -> str:
    """执行一个完整 Agent 回合，期间最多执行 3 次工具。"""
    if not messages:
        raise ValueError("会话不能为空")

    config = load_model_config()
    client = create_deepseek_client(config)
    tool_call_count = 0

    while True:
        message = request_completion(client, config, messages)
        tool_calls = message.tool_calls

        # 没有工具请求时，本轮得到最终自然语言回答。
        if not tool_calls:
            answer = message.content

            if not answer:
                logger.warning("模型返回空回答")
                raise LLMClientError("模型未返回有效回答，请稍后再试")

            messages.append(
                {
                    "role": "assistant",
                    "content": answer,
                }
            )
            logger.info("模型请求成功")
            return answer

        # 在执行工具前检查总次数，保证上限不会被突破。
        if tool_call_count + len(tool_calls) > MAX_TOOL_CALLS:
            logger.warning(
                "工具调用次数超过上限: current=%d, requested=%d, limit=%d",
                tool_call_count,
                len(tool_calls),
                MAX_TOOL_CALLS,
            )
            raise LLMClientError(
                f"工具调用次数超过上限: {MAX_TOOL_CALLS}"
            )

        # 先把模型的工具请求加入历史，再加入每个工具结果。
        messages.append(serialize_assistant_tool_message(message))

        for tool_call in tool_calls:
            tool_name = tool_call.function.name
            raw_arguments = tool_call.function.arguments

            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                tool_result = "工具执行失败: 参数不是有效 JSON"
            else:
                if not isinstance(arguments, dict):
                    tool_result = "工具执行失败: 参数必须是 JSON 对象"
                else:
                    # 只展示工具名称，不展示模型传入的参数。
                    print(f"正在使用工具: {tool_name}")
                    tool_result = execute_tool(tool_name, arguments)

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                }
            )
            tool_call_count += 1