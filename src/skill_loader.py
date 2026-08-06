import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIRECTORY = (PROJECT_ROOT / "skills").resolve()

MAX_SKILL_FILE_BYTES = 10_000
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")


class SkillLoadError(RuntimeError):
    """表示技能文件不存在、越界或格式无效。"""


def get_skill_path(skill_name: str) -> Path:
    """验证技能名，并返回受限 skills 目录中的 SKILL.md 路径。"""
    if not SKILL_NAME_PATTERN.fullmatch(skill_name):
        raise SkillLoadError("技能名称只能包含小写字母、数字和连字符")

    skill_path = (SKILLS_DIRECTORY / skill_name / "SKILL.md").resolve()

    try:
        skill_path.relative_to(SKILLS_DIRECTORY)
    except ValueError as error:
        raise SkillLoadError("技能路径超出允许目录") from error

    return skill_path


def validate_skill_content(content: str, expected_name: str) -> None:
    """校验最小前置元数据，避免把任意 Markdown 当作可执行技能。"""
    frontmatter_match = re.match(
        r"\A---\n(?P<frontmatter>.*?)\n---\n",
        content,
        re.DOTALL,
    )

    if frontmatter_match is None:
        raise SkillLoadError("技能文件缺少 YAML 前置元数据")

    frontmatter = frontmatter_match.group("frontmatter")

    name_match = re.search(
        r"^name:\s*(?P<name>[a-z0-9-]+)\s*$",
        frontmatter,
        re.MULTILINE,
    )
    description_match = re.search(
        r"^description:\s*(?P<description>.+)\s*$",
        frontmatter,
        re.MULTILINE,
    )

    if name_match is None or name_match.group("name") != expected_name:
        raise SkillLoadError("技能文件的 name 与请求技能不一致")

    if description_match is None:
        raise SkillLoadError("技能文件缺少 description")

    if not content[frontmatter_match.end():].strip():
        raise SkillLoadError("技能文件缺少具体工作流内容")


def load_skill(skill_name: str) -> str:
    """读取并验证一个仓库内技能，返回其完整说明文本。"""
    skill_path = get_skill_path(skill_name)

    if not skill_path.exists():
        raise SkillLoadError(f"技能不存在: {skill_name}")

    if not skill_path.is_file():
        raise SkillLoadError("技能路径不是普通文件")

    try:
        if skill_path.stat().st_size > MAX_SKILL_FILE_BYTES:
            raise SkillLoadError("技能文件超过读取上限")

        content = skill_path.read_text(encoding="utf-8")
    except OSError as error:
        logger.error("读取技能文件失败: error_type=%s", type(error).__name__)
        raise SkillLoadError("无法读取技能文件") from error

    validate_skill_content(content, skill_name)
    logger.info("技能加载成功: skill_name=%s", skill_name)
    return content