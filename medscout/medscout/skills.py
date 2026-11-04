"""Skill library (Lec 4 "Skills", "Skill package", "Skill loading").

skills/<name>/SKILL.md  — YAML-style front matter (name, description) + procedure
skills/<name>/references/*.md — detailed material read on demand

Discover : names + descriptions go into the stable prompt prefix (skill index)
Activate : the planner assigns a skill to each task; its SKILL.md body is
           loaded into that task's context only ("load when needed", Lec 5)
Use      : the model may call read_skill_reference(skill, file) for references
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    references: list[str] = field(default_factory=list)


def _front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    _, fm, body = text.split("---", 2)
    meta = {}
    for line in fm.strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, body.strip()


class SkillLibrary:
    def __init__(self, skills_dir: Path):
        self.dir = skills_dir
        self.skills: dict[str, Skill] = {}
        for md in sorted(skills_dir.glob("*/SKILL.md")):
            meta, body = _front_matter(md.read_text())
            name = meta.get("name", md.parent.name)
            refs = sorted(str(p.relative_to(md.parent)) for p in (md.parent / "references").glob("*.md")) \
                if (md.parent / "references").exists() else []
            self.skills[name] = Skill(name, meta.get("description", ""), body, md.parent, refs)

    def names(self) -> list[str]:
        return sorted(self.skills)

    def index_text(self) -> str:
        return "\n".join(f"- {s.name}: {s.description}" for s in self.skills.values())

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def activation_text(self, name: str) -> str:
        s = self.skills.get(name)
        if s is None:
            return ""
        refs = ("\nReferences available via read_skill_reference: " + ", ".join(s.references)) if s.references else ""
        return f"LOADED_SKILL: {s.name}\n{s.body}{refs}"

    def read_reference(self, name: str, file: str) -> str:
        from .tools.base import ToolError
        s = self.skills.get(name)
        if s is None:
            raise ToolError("permanent", f"unknown skill {name}; available: {self.names()}")
        path = (s.path / file).resolve()
        if s.path.resolve() not in path.parents or not path.exists():   # no path traversal
            raise ToolError("permanent", f"no reference '{file}' in skill {name}; available: {s.references}")
        return path.read_text()
