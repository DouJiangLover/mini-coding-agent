from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AGENT_MODES = {"safe", "standard", "autonomous", "read_only"}


@dataclass(frozen=True)
class AgentSettings:
    mode: str = "standard"
    max_steps: int = 45
    failure_limit: int = 3
    interaction_first: bool = True
    require_verification: bool = True
    require_review: bool = True
    context_budget: int = 48_000
    command_timeout: int = 30
    updated_at: str = ""

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgentSettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._settings = self._load()

    def get(self) -> AgentSettings:
        return self._settings

    def update(self, values: dict[str, Any]) -> AgentSettings:
        current = self._settings.public_dict()
        current.update(values)
        current["updated_at"] = datetime.now(timezone.utc).astimezone().isoformat()
        self._settings = self._validate(current)
        self._persist()
        return self._settings

    def reset(self) -> AgentSettings:
        self._settings = AgentSettings(
            updated_at=datetime.now(timezone.utc).astimezone().isoformat(),
        )
        self._persist()
        return self._settings

    def _load(self) -> AgentSettings:
        if not self.path or not self.path.is_file():
            return AgentSettings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return AgentSettings()
            return self._validate(data)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return AgentSettings()

    @staticmethod
    def _validate(values: dict[str, Any]) -> AgentSettings:
        mode = str(values.get("mode", "standard"))
        if mode not in AGENT_MODES:
            raise ValueError("未知的 Agent 运行模式")

        def bounded_integer(name: str, default: int, minimum: int, maximum: int) -> int:
            raw = values.get(name, default)
            if isinstance(raw, bool):
                raise ValueError(f"{name} 必须是整数")
            try:
                parsed = int(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} 必须是整数") from exc
            if not minimum <= parsed <= maximum:
                raise ValueError(f"{name} 必须在 {minimum}–{maximum} 之间")
            return parsed

        def boolean(name: str, default: bool) -> bool:
            raw = values.get(name, default)
            if not isinstance(raw, bool):
                raise ValueError(f"{name} 必须是布尔值")
            return raw

        return AgentSettings(
            mode=mode,
            max_steps=bounded_integer("max_steps", 45, 5, 100),
            failure_limit=bounded_integer("failure_limit", 3, 1, 10),
            interaction_first=boolean("interaction_first", True),
            require_verification=boolean("require_verification", True),
            require_review=boolean("require_review", True),
            context_budget=bounded_integer("context_budget", 48_000, 12_000, 200_000),
            command_timeout=bounded_integer("command_timeout", 30, 5, 60),
            updated_at=str(values.get("updated_at", "")),
        )

    def _persist(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._settings.public_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
