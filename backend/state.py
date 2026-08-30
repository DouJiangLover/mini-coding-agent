from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class RunRecord:
    run_id: str
    task: str
    workspace: str
    requested_skill: str = "auto"
    status: str = "created"
    phase: str = "created"
    summary: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).astimezone().isoformat())
    cancel_requested: bool = False
    background_task: asyncio.Task[Any] | None = None
    changed_files: list[str] = field(default_factory=list)
    successful_commands: list[str] = field(default_factory=list)
    pending_approval: dict[str, Any] | None = None
    approval_future: asyncio.Future[str] | None = field(default=None, repr=False)
    pending_interaction: dict[str, Any] | None = None
    interaction_future: asyncio.Future[dict[str, str]] | None = field(default=None, repr=False)
    pending_skill_selection: dict[str, Any] | None = None
    skill_selection_future: asyncio.Future[str] | None = field(default=None, repr=False)

    def public_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task": self.task,
            "workspace": self.workspace,
            "requested_skill": self.requested_skill,
            "status": self.status,
            "phase": self.phase,
            "summary": self.summary,
            "created_at": self.created_at,
            "changed_files": self.changed_files,
            "successful_commands": self.successful_commands,
            "pending_approval": self.pending_approval,
            "pending_interaction": self.pending_interaction,
            "pending_skill_selection": self.pending_skill_selection,
        }

    def begin_approval(self, approval: dict[str, Any]) -> asyncio.Future[str]:
        if self.pending_approval or (self.approval_future and not self.approval_future.done()):
            raise RuntimeError("已有待处理的授权请求")
        self.pending_approval = approval
        self.approval_future = asyncio.get_running_loop().create_future()
        return self.approval_future

    def resolve_approval(self, approval_id: str, decision: str) -> bool:
        if not self.pending_approval or self.pending_approval.get("approval_id") != approval_id:
            return False
        if not self.approval_future or self.approval_future.done():
            return False
        self.approval_future.set_result(decision)
        return True

    def clear_approval(self) -> None:
        self.pending_approval = None
        self.approval_future = None

    def begin_interaction_confirmation(self, model: dict[str, Any]) -> asyncio.Future[dict[str, str]]:
        if self.pending_interaction or (self.interaction_future and not self.interaction_future.done()):
            raise RuntimeError("已有待确认的交互模型")
        self.pending_interaction = model
        self.interaction_future = asyncio.get_running_loop().create_future()
        return self.interaction_future

    def resolve_interaction_confirmation(
        self,
        model_id: str,
        decision: str,
        feedback: str = "",
    ) -> bool:
        if not self.pending_interaction or self.pending_interaction.get("model_id") != model_id:
            return False
        if not self.interaction_future or self.interaction_future.done():
            return False
        self.interaction_future.set_result({"decision": decision, "feedback": feedback})
        return True

    def clear_interaction_confirmation(self) -> None:
        self.pending_interaction = None
        self.interaction_future = None

    def begin_skill_confirmation(self, selection: dict[str, Any]) -> asyncio.Future[str]:
        if self.pending_skill_selection or (self.skill_selection_future and not self.skill_selection_future.done()):
            raise RuntimeError("已有待确认的 Skill 选择")
        self.pending_skill_selection = selection
        self.skill_selection_future = asyncio.get_running_loop().create_future()
        return self.skill_selection_future

    def resolve_skill_confirmation(self, selection_id: str, skill_name: str) -> bool:
        if not self.pending_skill_selection or self.pending_skill_selection.get("selection_id") != selection_id:
            return False
        candidates = {
            str(item.get("name"))
            for item in self.pending_skill_selection.get("candidates", [])
            if isinstance(item, dict)
        }
        if skill_name != "cancelled" and skill_name not in candidates:
            return False
        if not self.skill_selection_future or self.skill_selection_future.done():
            return False
        self.skill_selection_future.set_result(skill_name)
        return True

    def clear_skill_confirmation(self) -> None:
        self.pending_skill_selection = None
        self.skill_selection_future = None


class RunStore:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}

    def add(self, run: RunRecord) -> None:
        self._runs[run.run_id] = run

    def get(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def list_recent(self) -> list[RunRecord]:
        return sorted(self._runs.values(), key=lambda run: run.created_at, reverse=True)

    def has_active_workspace(self, workspace: str) -> bool:
        return any(
            workspaces_overlap(run.workspace, workspace) and run.status in {
                "created", "running", "waiting_skill_confirmation", "waiting_approval", "waiting_interaction_confirmation",
            }
            for run in self._runs.values()
        )


def workspaces_overlap(first: str, second: str) -> bool:
    first_parts = tuple(part for part in PurePosixPath(first).parts if part not in {"", "."})
    second_parts = tuple(part for part in PurePosixPath(second).parts if part not in {"", "."})
    shared_length = min(len(first_parts), len(second_parts))
    return first_parts[:shared_length] == second_parts[:shared_length]
