from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


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


if __name__ == "__main__":
    raise SystemExit(main())