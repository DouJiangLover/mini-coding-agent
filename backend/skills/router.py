from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Skill:
    name: str
    display_name: str
    description: str
    keywords: list[str]
    allowed_tools: list[str]
    plan: list[dict[str, Any]]
    prompt: str


@dataclass(frozen=True)
class SkillMatch:
    skill: Skill
    score: int
    matched_keywords: list[str]

    @property
    def reason(self) -> str:
        if self.matched_keywords:
            return f"匹配关键词：{'、'.join(self.matched_keywords[:4])}"
        return "未命中特定领域，采用通用编程策略"


class SkillRouter:
    def __init__(self, skills_root: Path) -> None:
        self.skills = self._load(skills_root)
        if not self.skills:
            raise RuntimeError(f"没有在 {skills_root} 中发现可用 Skill")

    def select(self, task: str) -> SkillMatch:
        normalized = task.casefold()
        scored: list[SkillMatch] = []
        for skill in self.skills:
            matched = [keyword for keyword in skill.keywords if keyword.casefold() in normalized]
            score = sum(max(1, len(re.findall(re.escape(keyword.casefold()), normalized))) for keyword in matched)
            scored.append(SkillMatch(skill=skill, score=score, matched_keywords=matched))
        scored.sort(key=lambda item: (item.score, item.skill.name == "bug_fix"), reverse=True)
        return scored[0]

    @staticmethod
    def _load(root: Path) -> list[Skill]:
        skills: list[Skill] = []
        for metadata_path in sorted(root.glob("*/skill.json")):
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            prompt_path = metadata_path.parent / "prompt.md"
            skills.append(Skill(
                name=data["name"],
                display_name=data.get("display_name", data["name"]),
                description=data["description"],
                keywords=list(data.get("keywords", [])),
                allowed_tools=list(data["allowed_tools"]),
                plan=list(data["plan"]),
                prompt=prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else "",
            ))
        return skills
