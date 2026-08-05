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
    provider: str
    model: str
    api_key: str


def get_required_setting(name: str) -> str:
    value = os.getenv(name, "").strip()

    if not value:
        raise ValueError(f"缺少必备配置: {name}")

    return value


def load_model_config() -> ModelConfig:
    return ModelConfig(
        provider = get_required_setting("LLM_PROVIDER"),
        model = get_required_setting("LLM_MODEL"),
        api_key = get_required_setting("LLM_API_KEY"),
    )


def describe_model_config() -> str:
    config = load_model_config()
    
    return "\n".join(
        [
            f"模型供应商: {config.provider}",
            f"模型名称: {config.model}",
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