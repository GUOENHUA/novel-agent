"""Skill loader — discovers and loads skills from bundled and disk sources.

Borrowed from claude-code `src/skills/loadSkillsDir.ts` and
hermes-agent `tools/skills_tool.py`.

Loading order (first wins):
  1. Bundled skills (built into novel_agent/skills/bundled/)
  2. Project-level skills (.novel-agent/skills/)
  3. User-level skills (~/.novel-agent/skills/)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from novel_agent.utils.files import read_file_safe

logger = logging.getLogger(__name__)

SKILL_FILE = "SKILL.md"

# Limits from Anthropic's progressive disclosure recommendations
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024


class SkillInfo:
    """Metadata for a discovered skill."""

    def __init__(
        self,
        name: str,
        description: str,
        path: Path,
        category: str = "",
        source: str = "bundled",
    ):
        self.name = name[:MAX_NAME_LENGTH]
        self.description = description[:MAX_DESCRIPTION_LENGTH]
        self.path = path
        self.category = category
        self.source = source  # "bundled", "project", "user"

    def load_content(self) -> Optional[str]:
        """Load the full skill content."""
        content = read_file_safe(self.path / SKILL_FILE)
        if not content:
            content = read_file_safe(Path(str(self.path) + ".md"))
        return content


class SkillLoader:
    """Discovers and loads skills from multiple sources."""

    def __init__(self):
        self._skills: dict[str, SkillInfo] = {}

    def discover_all(self, project_dir: str | Path | None = None) -> list[SkillInfo]:
        """Discover skills from all sources. First wins on name collision."""
        self._skills.clear()

        # 1. Bundled skills
        bundled_dir = Path(__file__).parent / "bundled"
        self._discover_from_dir(bundled_dir, "bundled")

        # 2. Project-level skills
        if project_dir:
            project_skills = Path(project_dir) / ".novel-agent" / "skills"
            self._discover_from_dir(project_skills, "project")

        # 3. User-level skills
        user_skills = Path.home() / ".novel-agent" / "skills"
        self._discover_from_dir(user_skills, "user")

        return self.list_all()

    def _discover_from_dir(self, root: Path, source: str) -> None:
        """Discover skills from a directory. Skill = dir/SKILL.md."""
        if not root.exists():
            return

        for skill_dir in sorted(root.iterdir()):
            if not skill_dir.is_dir():
                continue

            skill_md = skill_dir / SKILL_FILE
            if not skill_md.exists():
                continue

            content = read_file_safe(skill_md)
            if not content:
                continue

            name, description = self._parse_frontmatter(content, skill_dir.name)

            if name not in self._skills:  # First wins
                self._skills[name] = SkillInfo(
                    name=name,
                    description=description,
                    path=skill_dir,
                    category=skill_dir.parent.name if skill_dir.parent != root else "",
                    source=source,
                )

    def list_all(self) -> list[SkillInfo]:
        """List all discovered skills."""
        return sorted(self._skills.values(), key=lambda s: s.name)

    def get(self, name: str) -> Optional[SkillInfo]:
        """Get a skill by name."""
        return self._skills.get(name)

    def build_system_prompt_block(self) -> str:
        """Build a compact skills listing for the system prompt."""
        skills = self.list_all()
        if not skills:
            return ""

        lines = ["## 可用写作 Skills", ""]
        for s in skills:
            lines.append(f"- **{s.name}**: {s.description}")
        lines.append("")
        lines.append("使用 skill_view 工具加载完整 skill 内容。")
        return "\n".join(lines)

    @staticmethod
    def _parse_frontmatter(content: str, fallback_name: str) -> tuple[str, str]:
        """Parse YAML frontmatter to extract name and description."""
        name = fallback_name
        description = ""

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                for line in parts[1].strip().split("\n"):
                    if ":" in line:
                        key, _, value = line.partition(":")
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        if key == "name":
                            name = value
                        elif key == "description":
                            description = value

        # Fallback: first non-heading line as description
        if not description:
            for line in content.split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("---"):
                    description = line[:MAX_DESCRIPTION_LENGTH]
                    break

        return name, description
