from pathlib import Path
import sys

project_root = Path(__file__).resolve().parent.parent

print(f"Project 解释器: {sys.executable}")
print(f"Project 版本: {sys.version.split()[0]}")
print(f"项目根目录: {project_root}")