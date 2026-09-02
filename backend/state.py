from __future__ import annotations

import asyncio
import uuid
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
    root_task: str = ""
    parent_run_id: str | None = None
    continuation_context: dict[str, Any] | None = None
    session_id: str = ""
    session_entry_id: str = ""
    parent_entry_id: str | None = None
    status: str = "created"
    phase: str = "created"
    summary: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).astimezone().isoformat())
    cancel_requested: bool = False
    background_task: asyncio.Task[Any] | None = None
    changed_files: list[str] = field(default_factory=list)
    read_files: list[str] = field(default_factory=list)
    successful_commands: list[str] = field(default_factory=list)
    traceability: dict[str, Any] | None = None
    user_guide_path: str = ""
    steering_messages: list[dict[str, Any]] = field(default_factory=list)
    pending_approval: dict[str, Any] | None = None
    approval_future: asyncio.Future[str] | None = field(default=None, repr=False)
    pending_interaction: dict[str, Any] | None = None
    interaction_future: asyncio.Future[dict[str, str]] | None = field(default=None, repr=False)
    pending_skill_selection: dict[str, Any] | None = None
    skill_selection_future: asyncio.Future[str] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.root_task:
            self.root_task = self.task

    def public_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task": self.task,
            "workspace": self.workspace,
            "requested_skill": self.requested_skill,
            "root_task": self.root_task,
            "parent_run_id": self.parent_run_id,
            "session_id": self.session_id,
            "session_entry_id": self.session_entry_id,
            "parent_entry_id": self.parent_entry_id,
            "status": self.status,
            "phase": self.phase,
            "summary": self.summary,
            "created_at": self.created_at,
            "changed_files": self.changed_files,
            "read_files": self.read_files,
            "successful_commands": self.successful_commands,
            "traceability": self.traceability,
            "user_guide_path": self.user_guide_path,
            "steering_messages": self.steering_messages,
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

    def enqueue_steering(self, message: str) -> dict[str, Any]:
        pending = sum(item.get("status") == "queued" for item in self.steering_messages)
        if pending >= 20:
            raise ValueError("待处理的 Steering 消息过多，请等待 Agent 消化后再发送")
        item = {
            "steering_id": f"steer_{uuid.uuid4().hex[:12]}",
            "message": message.strip(),
            "status": "queued",
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "applied_at": "",
        }
        self.steering_messages.append(item)
        return item

    def take_pending_steering(self) -> list[dict[str, Any]]:
        applied: list[dict[str, Any]] = []
        applied_at = datetime.now(timezone.utc).astimezone().isoformat()
        for item in self.steering_messages:
            if item.get("status") != "queued":
                continue
            item["status"] = "applied"
            item["applied_at"] = applied_at
            applied.append(dict(item))
        return applied

    def has_pending_steering(self) -> bool:
        return any(item.get("status") == "queued" for item in self.steering_messages)


class RunStore:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}

    def add(self, run: RunRecord) -> None:
        self._runs[run.run_id] = run

    def get(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def list_recent(self) -> list[RunRecord]:
        return sorted(self._runs.values(), key=lambda run: run.created_at, reverse=True)

    def latest_for_workspace(self, workspace: str) -> RunRecord | None:
        return next((run for run in self.list_recent() if run.workspace == workspace), None)

    def restore(self, channels: list[Any]) -> None:
        for channel in channels:
            run_events = channel.events
            started = next((event for event in run_events if event.get("type") == "run_started"), None)
            if not started:
                continue
            payload = started.get("payload", {})
            finished = next(
                (event for event in reversed(run_events) if event.get("type") == "run_finished"),
                None,
            )
            finished_payload = finished.get("payload", {}) if finished else {}
            task = str(payload.get("task", "")).strip()
            workspace = str(payload.get("workspace", ".")).strip() or "."
            if not task:
                continue
            record = RunRecord(
                run_id=str(started.get("run_id", channel.run_id)),
                task=task,
                workspace=workspace,
                requested_skill=str(payload.get("requested_skill", "auto")),
                root_task=str(payload.get("root_task", task)),
                parent_run_id=str(payload["parent_run_id"]) if payload.get("parent_run_id") else None,
                continuation_context=payload.get("continuation_context") if isinstance(payload.get("continuation_context"), dict) else None,
                session_id=str(payload.get("session_id", "")),
                session_entry_id=str(payload.get("session_entry_id", "")),
                parent_entry_id=str(payload["parent_entry_id"]) if payload.get("parent_entry_id") else None,
                status=str(finished_payload.get("status", "failed")),
                phase=str(finished.get("phase", "failed")) if finished else "failed",
                summary=str(finished.get("summary", "任务已中断")) if finished else "任务已中断",
                created_at=str(started.get("timestamp", "")),
                changed_files=[str(item) for item in finished_payload.get("changed_files", [])],
                read_files=[str(item) for item in finished_payload.get("read_files", [])],
                successful_commands=[str(item) for item in finished_payload.get("successful_commands", [])],
                traceability=finished_payload.get("traceability") if isinstance(finished_payload.get("traceability"), dict) else None,
                user_guide_path=str(finished_payload.get("user_guide_path", "")),
                steering_messages=[
                    dict(item) for item in finished_payload.get("steering_messages", [])
                    if isinstance(item, dict)
                ],
            )
            self.add(record)

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
