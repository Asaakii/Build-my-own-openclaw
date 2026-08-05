from pathlib import Path


# 根据当前文件位置定位项目根目录，避免依赖终端当前所在目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 人格配置固定放在工作区中，后续会继续在这里放技能和记忆
SOUL_PATH = PROJECT_ROOT / "workspace" / "SOUL.md"


def load_soul() -> str:
    """读取 SOUL.md 文件内容，返回作为 system 消息的文本。"""
    if not SOUL_PATH.exists():
        raise FileNotFoundError(
            "缺少人格配置文件: workspace/SOUL.md"
        )

    # 明确使用 UTF-8 编码读取，避免不同系统默认编码不一致导致的乱码
    content = SOUL_PATH.read_text(encoding="utf-8").strip()

    if not content:
        raise ValueError(
            "SOUL 文件内容不能为空，请在 workspace/SOUL.md 中写入人格配置。"
        )

    return content