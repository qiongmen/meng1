"""Skill 发现、解析与动态加载。

约定：
- 项目根目录 = 进程启动时的工作目录（Path.cwd()）
- Skill 存放位置 = <项目根目录>/.agents/skills/<skill_name>/{SKILL.md, ...}
- SKILL.md 以 YAML frontmatter 开头，必需字段: name, description
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - yaml 已在 dist 依赖里
    yaml = None


# ---- YAML frontmatter 解析 ---------------------------------------------------

_FRONTMATTER_RE = re.compile(
    r'^---\s*\n(.*?)\n---\s*\n?', re.DOTALL
)


def _normalize_frontmatter(meta_text: str) -> str:
    """把常见的"不规范 YAML"修正一下：中文冒号、缺少空格等。"""
    # 中文冒号 → 英文冒号
    meta_text = meta_text.replace('：', ':')
    # key:value → key: value（冒号后没空格时补一个）
    meta_text = re.sub(r':(\S)', r': \1', meta_text)
    return meta_text


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """把 '---\\nmeta\\n---\\nbody' 拆成 (meta_dict, body_str)。

    没有 frontmatter 时返回 ({}, text)。
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta_text = _normalize_frontmatter(m.group(1))
    body = text[m.end():]

    meta: dict = {}
    if yaml is not None:
        try:
            parsed = yaml.safe_load(meta_text)
            if isinstance(parsed, dict):
                meta = parsed
        except Exception:
            pass

    # yaml 没解析出来时，用极简 fallback 逐行拆
    if not meta:
        for line in meta_text.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if ':' in line:
                k, v = line.split(':', 1)
                k = k.strip()
                v = v.strip()
                # 去掉值两端的引号
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                    v = v[1:-1]
                meta[k] = v

    if not isinstance(meta, dict):
        meta = {}
    return meta, body


# ---- Skill 数据结构 ----------------------------------------------------------

@dataclass
class SkillInfo:
    """轻量的 Skill 元信息（启动时只加载 name/description）。"""
    name: str
    description: str
    skill_dir: Path  # skill 子目录绝对路径
    skill_md: Path   # SKILL.md 绝对路径

    @property
    def directory(self) -> Path:
        return self.skill_dir


@dataclass
class SkillRegistry:
    """管理所有已发现的 Skill。"""
    root: Path                               # .agents/skills 的父目录（即项目根）
    skills_dir: Path                         # .agents/skills/
    _skills: dict[str, SkillInfo] = field(default_factory=dict)

    # -- 发现 ------------------------------------------------------------------

    @classmethod
    def discover(cls, project_root: Optional[Path] = None,
                 extra_dirs: Optional[list[Path]] = None) -> 'SkillRegistry':
        """扫描 <project_root>/.agents/skills/ 以及 extra_dirs 下所有子目录。

        project_root 默认取 Path.cwd()。
        """
        root = Path(project_root) if project_root else Path.cwd()
        skills_dir = root / '.agents' / 'skills'
        registry = cls(root=root, skills_dir=skills_dir)

        all_dirs: list[Path] = []
        if skills_dir.is_dir():
            all_dirs.append(skills_dir)
        if extra_dirs:
            for d in extra_dirs:
                if d and d.is_dir() and d not in all_dirs:
                    all_dirs.append(d)

        seen_mds: set[Path] = set()
        for scan_dir in all_dirs:
            for skill_md in scan_dir.rglob('*'):
                if not skill_md.is_file():
                    continue
                if skill_md.name.lower() != 'skill.md':
                    continue
                try:
                    resolved = skill_md.resolve()
                except Exception:
                    continue
                if resolved in seen_mds:
                    continue
                seen_mds.add(resolved)
                try:
                    text = skill_md.read_text(encoding='utf-8')
                    meta, _ = parse_frontmatter(text)
                    name = (meta.get('name') or '').strip()
                    desc = (meta.get('description') or '').strip()
                    if not name or not desc:
                        continue
                except Exception:
                    continue
                skill_dir = resolved.parent
                registry._skills[name] = SkillInfo(
                    name=name,
                    description=desc,
                    skill_dir=skill_dir,
                    skill_md=resolved,
                )
        return registry

    # -- 查询 ------------------------------------------------------------------

    @property
    def all(self) -> list[SkillInfo]:
        return list(self._skills.values())

    def get(self, name: str) -> Optional[SkillInfo]:
        return self._skills.get(name)

    def names_and_descriptions(self) -> list[tuple[str, str]]:
        return [(s.name, s.description) for s in self._skills.values()]

    # -- 加载内容 --------------------------------------------------------------

    def load_skill_md(self, name: str) -> Optional[str]:
        """返回 SKILL.md 全文（含 frontmatter），未找到返回 None。"""
        info = self._skills.get(name)
        if not info:
            return None
        try:
            return info.skill_md.read_text(encoding='utf-8')
        except Exception:
            return None

    def resolve_file(self, skill_name: str, filename: str) -> tuple[Optional[Path], list[Path]]:
        """在指定 skill 的目录内解析 filename。

        返回 (resolved_path_or_None, candidate_list)。
        - 精确匹配到 skill 目录内的路径 → 直接返回
        - 只给文件名 → 递归搜 skill 目录，唯一命中返回，多命中返回 candidates
        - 越界（不在 skill 目录里）→ 返回 (None, [])
        """
        info = self._skills.get(skill_name)
        if not info:
            return None, []

        skill_dir = info.skill_dir.resolve()
        candidate = Path(filename)

        # 1) 绝对路径或项目相对路径 → 先尝试直接解析
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            # 相对于 skill 目录
            resolved = (skill_dir / candidate).resolve()

        # 越界检查
        try:
            resolved.relative_to(skill_dir)
        except ValueError:
            return None, []

        if resolved.is_file():
            return resolved, []

        # 2) 只给文件名 → 在 skill 目录内递归搜索
        stem = Path(filename).name
        hits: list[Path] = []
        for p in skill_dir.rglob(stem):
            if p.is_file():
                hits.append(p)
        if len(hits) == 1:
            return hits[0].resolve(), []
        if len(hits) > 1:
            return None, [h.resolve() for h in hits]
        return None, []


def discover_skills(project_root: Optional[Path] = None) -> SkillRegistry:
    """便捷函数。"""
    return SkillRegistry.discover(project_root)


# ---- System Prompt 片段生成 --------------------------------------------------

def build_skills_prompt(registry: SkillRegistry) -> str:
    """拼一段注入 System Prompt 的技能描述。"""
    items = registry.names_and_descriptions()
    if not items:
        return ''
    lines = ['您可以使用以下技能（Skill）：']
    for name, desc in items:
        lines.append(f"- '{name}': {desc}")
    lines.append('')
    lines.append('使用 loadSkill 工具可在需要时加载某个技能的完整说明。')
    lines.append('您还可以指定 filename 加载技能引用的其他文件。')
    return '\n'.join(lines)
