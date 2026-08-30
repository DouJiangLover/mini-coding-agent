from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AVAILABLE_SKILL_TOOLS = [
    "list_files",
    "read_file",
    "search_text",
    "create_file",
    "apply_patch",
    "run_command",
    "finish",
]

DEFAULT_CUSTOM_PLAN = [
    {"id": "inspect", "title": "理解项目结构与任务约束", "status": "running"},
    {"id": "baseline", "title": "运行现有检查建立基线", "status": "pending"},
    {"id": "diagnose", "title": "根据证据规划实现方案", "status": "pending"},
    {"id": "edit", "title": "实施聚焦修改", "status": "pending"},
    {"id": "verify", "title": "运行测试并验证结果", "status": "pending"},
    {"id": "review", "title": "完成前审查改动与遗漏", "status": "pending"},
]


@dataclass(frozen=True)
class Skill:
    name: str
    display_name: str
    description: str
    keywords: list[str]
    allowed_tools: list[str]
    plan: list[dict[str, Any]]
    prompt: str
    source: str = "built_in"
    created_at: str = ""

    def public_dict(self, *, enabled: bool) -> dict[str, Any]:
        return {**asdict(self), "enabled": enabled}


@dataclass(frozen=True)
class SkillMatch:
    skill: Skill
    score: int
    matched_keywords: list[str]

    @property
    def reason(self) -> str:
        if self.matched_keywords:
            return f"匹配关键词：{'、'.join(self.matched_keywords[:4])}"
        return "未命中特定领域，采用当前启用 Skill 中的通用策略"


class SkillRouter:
    def __init__(self, skills_root: Path, config_path: Path | None = None) -> None:
        self._built_in = self._load_built_in(skills_root)
        if not self._built_in:
            raise RuntimeError(f"没有在 {skills_root} 中发现可用 Skill")
        self.config_path = config_path
        self._custom: list[Skill] = []
        self._disabled: set[str] = set()
        self._load_config()

    @property
    def skills(self) -> list[Skill]:
        return [*self._built_in, *self._custom]

    @property
    def enabled_skills(self) -> list[Skill]:
        return [skill for skill in self.skills if skill.name not in self._disabled]

    def select(self, task: str) -> SkillMatch:
        return self.rank(task)[0]

    def rank(self, task: str, limit: int | None = None) -> list[SkillMatch]:
        enabled = self.enabled_skills
        if not enabled:
            raise RuntimeError("没有启用的 Skill，无法开始任务")
        normalized = task.casefold()
        scored: list[SkillMatch] = []
        for skill in enabled:
            matched = [keyword for keyword in skill.keywords if keyword.casefold() in normalized]
            score = sum(max(1, len(re.findall(re.escape(keyword.casefold()), normalized))) for keyword in matched)
            scored.append(SkillMatch(skill=skill, score=score, matched_keywords=matched))
        scored.sort(key=lambda item: (item.score, item.skill.name == "bug_fix"), reverse=True)
        return scored[:limit] if limit is not None else scored

    def match_enabled(self, name: str, task: str = "") -> SkillMatch:
        skill = next((item for item in self.enabled_skills if item.name == name), None)
        if not skill:
            raise KeyError(name)
        normalized = task.casefold()
        matched = [keyword for keyword in skill.keywords if keyword.casefold() in normalized]
        score = sum(max(1, len(re.findall(re.escape(keyword.casefold()), normalized))) for keyword in matched)
        return SkillMatch(skill=skill, score=score, matched_keywords=matched)

    def list_public(self) -> list[dict[str, Any]]:
        return [skill.public_dict(enabled=skill.name not in self._disabled) for skill in self.skills]

    def create_custom(
        self,
        *,
        display_name: str,
        description: str,
        keywords: list[str],
        allowed_tools: list[str],
        prompt: str,
    ) -> dict[str, Any]:
        display_name = display_name.strip()
        description = description.strip()
        prompt = prompt.strip()
        normalized_keywords = list(dict.fromkeys(keyword.strip() for keyword in keywords if keyword.strip()))
        normalized_tools = list(dict.fromkeys(allowed_tools))

        if not 2 <= len(display_name) <= 60:
            raise ValueError("Skill 名称需为 2–60 个字符")
        if not 4 <= len(description) <= 500:
            raise ValueError("适用场景说明需为 4–500 个字符")
        if not 1 <= len(normalized_keywords) <= 20:
            raise ValueError("请提供 1–20 个触发词")
        if any(len(keyword) > 40 for keyword in normalized_keywords):
            raise ValueError("单个触发词不能超过 40 个字符")
        if not 10 <= len(prompt) <= 4_000:
            raise ValueError("执行策略需为 10–4000 个字符")
        unknown_tools = set(normalized_tools) - set(AVAILABLE_SKILL_TOOLS)
        if unknown_tools:
            raise ValueError(f"包含未知工具：{', '.join(sorted(unknown_tools))}")
        if "finish" not in normalized_tools:
            normalized_tools.append("finish")
        if not any(tool in normalized_tools for tool in {"list_files", "read_file", "search_text"}):
            raise ValueError("自定义 Skill 至少需要一个项目观察工具")
        if any(skill.display_name.casefold() == display_name.casefold() for skill in self.skills):
            raise ValueError("已经存在同名 Skill")

        skill = Skill(
            name=f"custom_{uuid.uuid4().hex[:12]}",
            display_name=display_name,
            description=description,
            keywords=normalized_keywords,
            allowed_tools=normalized_tools,
            plan=[dict(item) for item in DEFAULT_CUSTOM_PLAN],
            prompt=prompt,
            source="custom",
            created_at=datetime.now(timezone.utc).astimezone().isoformat(),
        )
        self._custom.append(skill)
        self._persist()
        return skill.public_dict(enabled=True)

    def set_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        skill = next((item for item in self.skills if item.name == name), None)
        if not skill:
            raise KeyError(name)
        if not enabled and name not in self._disabled and len(self.enabled_skills) <= 1:
            raise ValueError("至少需要保留一个启用的 Skill")
        if enabled:
            self._disabled.discard(name)
        else:
            self._disabled.add(name)
        self._persist()
        return skill.public_dict(enabled=enabled)

    def _load_config(self) -> None:
        if not self.config_path or not self.config_path.is_file():
            return
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        known_built_in = {skill.name for skill in self._built_in}
        custom: list[Skill] = []
        for item in data.get("custom", []):
            if not isinstance(item, dict):
                continue
            try:
                skill = Skill(
                    name=str(item["name"]),
                    display_name=str(item["display_name"]),
                    description=str(item["description"]),
                    keywords=[str(value) for value in item["keywords"]],
                    allowed_tools=[str(value) for value in item["allowed_tools"]],
                    plan=[dict(value) for value in item.get("plan", DEFAULT_CUSTOM_PLAN)],
                    prompt=str(item["prompt"]),
                    source="custom",
                    created_at=str(item.get("created_at", "")),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if skill.name.startswith("custom_") and not (set(skill.allowed_tools) - set(AVAILABLE_SKILL_TOOLS)):
                custom.append(skill)
        self._custom = custom
        known_names = known_built_in | {skill.name for skill in custom}
        disabled = data.get("disabled", [])
        self._disabled = {str(name) for name in disabled if str(name) in known_names}
        if len(self._disabled) == len(known_names):
            self._disabled.discard(self._built_in[0].name)

    def _persist(self) -> None:
        if not self.config_path:
            return
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "disabled": sorted(self._disabled),
            "custom": [asdict(skill) for skill in self._custom],
        }
        temporary = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.config_path)

    @staticmethod
    def _load_built_in(root: Path) -> list[Skill]:
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
