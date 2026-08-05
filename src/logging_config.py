import logging
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "app.log"


def configure_logging() -> None:
    """配置仅写入本地文件的基础日志"""
    # logs/ 已被 .gitignore 忽略，不会进入版本控制
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        handlers=[
            logging.FileHandler(
                LOG_FILE,
                encoding="utf-8"
            )
        ],
        # 防止多次初始化时重复添加日志处理器
        force=True,
    )