import sys
from pathlib import Path


# pytest 从项目根目录运行时，也能像程序本身一样导入 src 内模块。
SRC_DIRECTORY = Path(__file__).resolve().parent.parent / "src"

if str(SRC_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SRC_DIRECTORY))