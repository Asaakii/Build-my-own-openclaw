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


# 这里固定使用 DeepSeek 作为供应商，后续可以扩展为支持其他供应商
# 使用 openai SDk 是因为 DeepSeek 兼容 OpenAI API，方便统一调用
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
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


def ask_model(messages: list[dict[str, str]]) -> str:
    """把完整回话历史发送给模型，并返回模型的回复。"""
    if not messages:
        raise ValueError("消息不能为空")

    # 每次请求的最后一条消息必须来自用户
    # 这样模型才能知道当前需要回答什么
    last_message = messages[-1]
    if last_message.get("role") != "user":
        raise ValueError("最后一条消息必须来自用户")

    if not last_message.get("content", "").strip():
        raise ValueError("最后一条消息内容不能为空")

    config = load_model_config()
    client = create_deepseek_client(config)

    # 只记录历史长度，不记录任何对话文本
    logger.info("开始模型请求：history_messages=%d", len(messages))

    try:
        response = client.chat.completions.create(
            model=config.model,
            # 这里不再只传一条用户信息， 而是传入完整历史
            messages=messages,
            # 首个版本限制回答长度， 便于控制成本
            max_tokens=512,
        )
    except APITimeoutError as error:
        # 必须先捕获超时，因为它属于APIConnectionError的一种
        logger.warning(
            "模型请求超时：timeout_seconds=%s",
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
            "模型服务返回错误状态码：status_code=%s",
            error.status_code,
        )
        raise LLMClientError(
            f"模型服务返回错误状态码，状态码: {error.status_code}"
        ) from error

    answer = response.choices[0].message.content

    # API 返回空内容时，不能假装模型回答过
    if not answer:
        logger.warning("模型返回空回答")
        raise LLMClientError("模型未返回有效回答，请稍后再试")


    # 只记录调用成功，不记录模型回答
    logger.info("模型请求成功")
    return answer