from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


# 无论从哪个目录运行程序，都根据当前文件位置找到项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 从项目根目录加载 .env 读取本机配置
# 。env 不会被提交到版本控制系统，避免泄露敏感信息
load_dotenv(PROJECT_ROOT / ".env")


# dataclass 用于定义模型配置类，包含供应商、模型名称和 API Key
# frozen=True 表示实例是不可变的，创建后不能修改属性值
@dataclass(frozen=True)
class ModelConfig:
    """保存调用大模型所需的配置。"""

    provider: str
    model: str
    api_key: str
    timeout_seconds: float


@dataclass(frozen=True)
class TelegramConnectionConfig:
    """保存验证 Telegram Bot 连接所需的最小配置。"""

    bot_token: str
    request_timeout_seconds: float


@dataclass(frozen=True)
class TelegramConfig:
    """保存 Telegram 长轮询运行时需要的完整配置。"""

    bot_token: str
    request_timeout_seconds: float
    poll_timeout_seconds: int
    allowed_user_id: int


def get_required_setting(name: str) -> str:
    """读取必填环境变量，并拒绝空值。"""
    value = os.getenv(name, "").strip()

    if not value:
        raise ValueError(f"缺少必备配置: {name}")

    return value


def get_positive_timeout_seconds(
    setting_name: str,
    default_value: str,
) -> float:
    """读取正数超时配置，避免网络请求无限等待。"""
    raw_value = os.getenv(setting_name, default_value).strip()

    try:
        timeout_seconds = float(raw_value)
    except ValueError as error:
        raise ValueError(
            f"{setting_name} 必须是大于 0 的数字。"
        ) from error

    if timeout_seconds <= 0:
        raise ValueError(
            f"{setting_name} 必须是大于 0 的数字。"
        )

    return timeout_seconds


def get_timeout_seconds() -> float:
    """读取模型请求超时。"""
    return get_positive_timeout_seconds(
        "LLM_TIMEOUT_SECONDS",
        "30",
    )


def get_telegram_request_timeout_seconds() -> float:
    """读取 Telegram API 请求超时。"""
    return get_positive_timeout_seconds(
        "TELEGRAM_REQUEST_TIMEOUT_SECONDS",
        "30",
    )


def get_telegram_poll_timeout_seconds() -> int:
    """读取长轮询等待时间，并限制为合理的正整数。"""
    raw_value = os.getenv(
        "TELEGRAM_POLL_TIMEOUT_SECONDS",
        "20",
    ).strip()

    try:
        timeout_seconds = int(raw_value)
    except ValueError as error:
        raise ValueError(
            "TELEGRAM_POLL_TIMEOUT_SECONDS 必须是正整数。"
        ) from error

    if not 1 <= timeout_seconds <= 50:
        raise ValueError(
            "TELEGRAM_POLL_TIMEOUT_SECONDS 必须在 1 到 50 之间。"
        )

    return timeout_seconds


def get_telegram_allowed_user_id() -> int:
    """读取唯一被允许使用 Bot 的 Telegram 用户 ID。"""
    raw_value = get_required_setting(
        "TELEGRAM_ALLOWED_USER_ID"
    )

    try:
        user_id = int(raw_value)
    except ValueError as error:
        raise ValueError(
            "TELEGRAM_ALLOWED_USER_ID 必须是正整数。"
        ) from error

    if user_id <= 0:
        raise ValueError(
            "TELEGRAM_ALLOWED_USER_ID 必须是正整数。"
        )

    return user_id


def load_model_config() -> ModelConfig:
    """将分散的环境变量整理为不可变配置对象"""
    return ModelConfig(
        provider = get_required_setting("LLM_PROVIDER"),
        model = get_required_setting("LLM_MODEL"),
        api_key = get_required_setting("LLM_API_KEY"),
        timeout_seconds = get_timeout_seconds(),
    )


def load_telegram_connection_config() -> TelegramConnectionConfig:
    """读取连接验证阶段需要的 Telegram 配置。"""
    return TelegramConnectionConfig(
        bot_token=get_required_setting("TELEGRAM_BOT_TOKEN"),
        request_timeout_seconds=get_telegram_request_timeout_seconds(),
    )


def load_telegram_config() -> TelegramConfig:
    """读取运行 Telegram Bot 所需的完整配置。"""
    connection_config = load_telegram_connection_config()
    poll_timeout_seconds = get_telegram_poll_timeout_seconds()

    # HTTP 客户端必须比长轮询多等一段时间，否则会先在本地超时。
    if connection_config.request_timeout_seconds <= poll_timeout_seconds:
        raise ValueError(
            "TELEGRAM_REQUEST_TIMEOUT_SECONDS 必须大于 "
            "TELEGRAM_POLL_TIMEOUT_SECONDS。"
        )

    return TelegramConfig(
        bot_token=connection_config.bot_token,
        request_timeout_seconds=connection_config.request_timeout_seconds,
        poll_timeout_seconds=poll_timeout_seconds,
        allowed_user_id=get_telegram_allowed_user_id(),
    )


def describe_model_config() -> str:
    """输出可安全展示的配置摘要，不输出 API key"""
    config = load_model_config()
    
    return "\n".join(
        [
            f"模型供应商: {config.provider}",
            f"模型名称: {config.model}",
            f"请求超时秒数：{config.timeout_seconds}",
            f"API Key: 已配置（已隐藏）",
        ]
    )


def main() -> int:
    try:
        print(describe_model_config())
    except ValueError as error:
        print(f"配置检查失败: {error}")
        return 1

    return 0


# 只有直接运行该文件时，才会执行 main 函数
# 未来其他文件 import 该模块时，不会执行 main 函数，避免不必要的输出
if __name__ == "__main__":
    raise SystemExit(main())