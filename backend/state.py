from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class RunRecord:
    run_id: str
    task: str
    workspace: str
    status: str = "created"
    phase: str = "created"
    summary: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).astimezone().isoformat())
    cancel_requested: bool = False
    background_task: asyncio.Task[Any] | None = None
    changed_files: list[str] = field(default_factory=list)
    successful_commands: list[str] = field(default_factory=list)

    def public_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task": self.task,
            "workspace": self.workspace,
            "status": self.status,
            "phase": self.phase,
            "summary": self.summary,
            "created_at": self.created_at,
            "changed_files": self.changed_files,
            "successful_commands": self.successful_commands,
        }


class RunStore:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}

    def add(self, run: RunRecord) -> None:
        self._runs[run.run_id] = run

    def get(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def has_active_workspace(self, workspace: str) -> bool:
        return any(
            run.workspace == workspace and run.status in {"created", "running"}
            for run in self._runs.values()
        )
