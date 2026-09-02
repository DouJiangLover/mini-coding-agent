from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import AsyncIterator, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from backend.agent.loop import AgentLoop
from backend.events.store import EventStore
from backend.llm.client import create_model_client
from backend.settings import AGENT_MODES, AgentSettingsStore
from backend.sessions.store import SessionStore
from backend.skills.importer import MAX_SKILL_UPLOAD_BYTES, SkillImportError, parse_skill_upload
from backend.skills.router import AVAILABLE_SKILL_TOOLS, SkillRouter
from backend.state import RunRecord, RunStore
from backend.workspace.catalog import (
    WorkspaceAlreadyExists,
    create_project_workspace,
    ensure_clean_workspace_root,
    move_workspace_to_trash,
)
from backend.workspace.guard import WorkspaceViolation, list_workspace_directories, resolve_workspace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
workspace_setting = Path(
    os.getenv("INTENTFLOW_WORKSPACE_ROOT")
    or os.getenv("TRACE_WORKSPACE_ROOT")
    or "workspaces"
)
WORKSPACE_ROOT = (workspace_setting if workspace_setting.is_absolute() else PROJECT_ROOT / workspace_setting).resolve()
RUNTIME_HOME = PROJECT_ROOT / ".intentflow"
LEGACY_RUNTIME_HOME = PROJECT_ROOT / ".tracecoder"
if not RUNTIME_HOME.exists() and LEGACY_RUNTIME_HOME.exists():
    # Preserve local history and settings when upgrading from the old product name.
    shutil.copytree(LEGACY_RUNTIME_HOME, RUNTIME_HOME)
RUNTIME_ROOT = RUNTIME_HOME / "runs"
SESSION_ROOT = RUNTIME_HOME / "sessions"
DEFAULT_WORKSPACE_ROOT = (PROJECT_ROOT / "workspaces").resolve()

if WORKSPACE_ROOT == DEFAULT_WORKSPACE_ROOT:
    ensure_clean_workspace_root(WORKSPACE_ROOT, PROJECT_ROOT / "examples")

events = EventStore(RUNTIME_ROOT)
runs = RunStore()
runs.restore(events.list_channels())
sessions = SessionStore(SESSION_ROOT)
sessions.migrate_runs(runs.list_recent())
model = create_model_client()
skills = SkillRouter(PROJECT_ROOT / "skills", RUNTIME_HOME / "skill-config.json")
agent_settings = AgentSettingsStore(RUNTIME_HOME / "agent-settings.json")
agent = AgentLoop(events, skills, model, settings_store=agent_settings, session_store=sessions)

app = FastAPI(
    title="IntentFlow API",
    description="A framework-free local coding agent core.",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in (
        os.getenv("INTENTFLOW_ORIGINS")
        or os.getenv("TRACECODER_ORIGINS")
        or "http://127.0.0.1:3000,http://localhost:3000"
    ).split(",")],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


class CreateRunRequest(BaseModel):
    task: str = Field(min_length=3, max_length=8_000)
    workspace: str = Field(default=".", min_length=1, max_length=500)
    skill: str = Field(default="auto", min_length=1, max_length=100)
    parent_run_id: str | None = Field(default=None, min_length=5, max_length=100)
    session_id: str | None = Field(default=None, min_length=5, max_length=100)
    parent_entry_id: str | None = Field(default=None, min_length=5, max_length=100)


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["allow", "deny"]


class InteractionDecisionRequest(BaseModel):
    decision: Literal["approve", "revise"]
    feedback: str = Field(default="", max_length=2_000)


class SkillSelectionDecisionRequest(BaseModel):
    skill_name: str = Field(min_length=1, max_length=100)


class SteeringRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2_000)


class CreateSkillRequest(BaseModel):
    display_name: str = Field(min_length=2, max_length=60)
    description: str = Field(min_length=4, max_length=500)
    keywords: list[str] = Field(min_length=1, max_length=20)
    allowed_tools: list[str] = Field(min_length=1, max_length=len(AVAILABLE_SKILL_TOOLS))
    prompt: str = Field(min_length=10, max_length=4_000)


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class SkillStatusRequest(BaseModel):
    enabled: bool


class AgentSettingsRequest(BaseModel):
    mode: Literal["safe", "standard", "autonomous", "read_only"]
    max_steps: int = Field(ge=5, le=100)
    failure_limit: int = Field(ge=1, le=10)
    interaction_first: bool
    require_verification: bool
    require_review: bool
    context_budget: int = Field(ge=12_000, le=200_000)
    command_timeout: int = Field(ge=5, le=60)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "mode": model.mode_name,
        "provider": model.provider_name,
        "model": model.model_name,
    }


@app.get("/api/skills")
async def list_skills() -> dict[str, object]:
    items = skills.list_public()
    return {
        "skills": items,
        "total": len(items),
        "enabled": sum(bool(item["enabled"]) for item in items),
        "available_tools": AVAILABLE_SKILL_TOOLS,
    }


@app.post("/api/skills", status_code=201)
async def create_skill(body: CreateSkillRequest) -> dict[str, object]:
    try:
        skill = skills.create_custom(
            display_name=body.display_name,
            description=body.description,
            keywords=body.keywords,
            allowed_tools=body.allowed_tools,
            prompt=body.prompt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"skill": skill}


@app.post("/api/skills/import", status_code=201)
async def import_skill(
    request: Request,
    filename: str = Query(..., min_length=1, max_length=255),
) -> dict[str, object]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_SKILL_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Skill 文件不能超过 1 MB")
        except ValueError:
            raise HTTPException(status_code=400, detail="文件大小信息不正确") from None

    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_SKILL_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Skill 文件不能超过 1 MB")
        chunks.append(chunk)
    payload = b"".join(chunks)
    try:
        imported = parse_skill_upload(filename, payload)
        skill = skills.create_custom(**imported.create_kwargs())
    except (SkillImportError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"skill": skill, "format": imported.source_format}


@app.post("/api/skills/{skill_name}/status")
async def update_skill_status(skill_name: str, body: SkillStatusRequest) -> dict[str, object]:
    try:
        skill = skills.set_enabled(skill_name, body.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Skill 不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"skill": skill}


@app.get("/api/settings")
async def get_agent_settings() -> dict[str, object]:
    return {
        "settings": agent_settings.get().public_dict(),
        "available_modes": sorted(AGENT_MODES),
    }


@app.post("/api/settings")
async def update_agent_settings(body: AgentSettingsRequest) -> dict[str, object]:
    try:
        updated = agent_settings.update(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"settings": updated.public_dict()}


@app.post("/api/settings/reset")
async def reset_agent_settings() -> dict[str, object]:
    return {"settings": agent_settings.reset().public_dict()}


@app.get("/api/workspaces")
async def browse_workspaces(path: str = Query(default=".", min_length=1, max_length=500)) -> dict[str, object]:
    try:
        current, directories = list_workspace_directories(WORKSPACE_ROOT, path)
    except WorkspaceViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def relative(directory: Path) -> str:
        return "." if directory == WORKSPACE_ROOT else directory.relative_to(WORKSPACE_ROOT).as_posix()

    current_path = relative(current)
    parent_path = None if current == WORKSPACE_ROOT else relative(current.parent)
    return {
        "root_path": str(WORKSPACE_ROOT),
        "current": current_path,
        "parent": parent_path,
        "directories": [
            {"name": directory.name, "path": relative(directory)}
            for directory in directories
        ],
    }


@app.post("/api/workspaces", status_code=201)
async def create_workspace(body: CreateWorkspaceRequest) -> dict[str, object]:
    try:
        workspace = create_project_workspace(WORKSPACE_ROOT, body.name)
    except WorkspaceAlreadyExists as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkspaceViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "workspace": {
            "name": workspace.name,
            "path": workspace.relative_to(WORKSPACE_ROOT).as_posix(),
        },
        "empty": True,
    }


@app.delete("/api/workspaces")
async def delete_workspace(path: str = Query(..., min_length=1, max_length=500)) -> dict[str, object]:
    try:
        workspace = resolve_workspace(WORKSPACE_ROOT, path)
    except WorkspaceViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if workspace == WORKSPACE_ROOT:
        raise HTTPException(status_code=400, detail="不能删除工作区根目录")
    normalized_workspace = workspace.relative_to(WORKSPACE_ROOT).as_posix()
    if runs.has_active_workspace(normalized_workspace):
        raise HTTPException(status_code=409, detail="该工作区仍有 Agent 任务运行，请等待任务结束后再删除")

    try:
        trash_location = move_workspace_to_trash(
            WORKSPACE_ROOT,
            RUNTIME_HOME / "workspace-trash",
            normalized_workspace,
        )
    except WorkspaceViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "status": "deleted",
        "workspace": normalized_workspace,
        "recoverable": True,
        "trash_location": trash_location.relative_to(PROJECT_ROOT).as_posix(),
    }


def _latest_event(run_id: str, event_type: str) -> dict[str, object] | None:
    channel = events.get(run_id)
    if not channel:
        return None
    return next(
        (event for event in reversed(channel.events) if event.get("type") == event_type),
        None,
    )


def _continuation_context(parent: RunRecord) -> dict[str, object]:
    selected_skill = _latest_event(parent.run_id, "skill_selected")
    plan_event = _latest_event(parent.run_id, "plan_updated")
    error_event = _latest_event(parent.run_id, "error")
    interaction_event = _latest_event(parent.run_id, "interaction_model_created")
    traceability_event = _latest_event(parent.run_id, "traceability_updated") or _latest_event(parent.run_id, "traceability_initialized")
    confirmed_interaction: dict[str, object] | None = None
    if interaction_event:
        model_id = str(interaction_event.get("payload", {}).get("model_id", ""))
        channel = events.get(parent.run_id)
        confirmed = next(
            (
                event for event in reversed(channel.events if channel else [])
                if event.get("type") == "interaction_confirmation_resolved"
                and str(event.get("payload", {}).get("model_id", "")) == model_id
                and event.get("payload", {}).get("decision") == "approve"
            ),
            None,
        )
        if confirmed:
            confirmed_interaction = dict(interaction_event.get("payload", {}))

    plan_items = plan_event.get("payload", {}).get("items", []) if plan_event else []
    structured_summary = sessions.latest_summary(parent.session_id, parent.session_entry_id) if parent.session_id else None
    return {
        "parent_run_id": parent.run_id,
        "root_task": parent.root_task,
        "previous_message": parent.task,
        "previous_status": parent.status,
        "previous_summary": parent.summary,
        "selected_skill": selected_skill.get("payload", {}).get("skill") if selected_skill else None,
        "selected_skills": selected_skill.get("payload", {}).get("skills", []) if selected_skill else [],
        "plan": plan_items if isinstance(plan_items, list) else [],
        "changed_files": list(parent.changed_files),
        "successful_commands": list(parent.successful_commands),
        "user_guide_path": parent.user_guide_path,
        "last_error": error_event.get("summary", "") if error_event else "",
        "confirmed_interaction_model": confirmed_interaction,
        "structured_session_summary": structured_summary,
        "traceability": (
            traceability_event.get("payload")
            if traceability_event and isinstance(traceability_event.get("payload"), dict)
            else parent.traceability
        ),
    }


def _conversation_chain(record: RunRecord) -> list[dict[str, object]]:
    if record.session_id and record.session_entry_id:
        path = sessions.path(record.session_id, record.session_entry_id)
        if path:
            return [{
                "run_id": entry.run_id,
                "session_entry_id": entry.entry_id,
                "parent_entry_id": entry.parent_id,
                "task": entry.task,
                "status": entry.status,
                "summary": entry.summary,
                "created_at": entry.created_at,
            } for entry in path]
    chain: list[dict[str, object]] = []
    current: RunRecord | None = record
    visited: set[str] = set()
    while current and current.run_id not in visited and len(chain) < 30:
        visited.add(current.run_id)
        chain.append({
            "run_id": current.run_id,
            "task": current.task,
            "status": current.status,
            "summary": current.summary,
            "created_at": current.created_at,
        })
        current = runs.get(current.parent_run_id) if current.parent_run_id else None
    chain.reverse()
    return chain


@app.post("/api/runs", status_code=202)
async def create_run(body: CreateRunRequest) -> dict[str, str]:
    try:
        workspace = resolve_workspace(WORKSPACE_ROOT, body.workspace)
    except WorkspaceViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    normalized_workspace = "." if workspace == WORKSPACE_ROOT else workspace.relative_to(WORKSPACE_ROOT).as_posix()
    if runs.has_active_workspace(normalized_workspace):
        raise HTTPException(
            status_code=409,
            detail="该工作区已有任务正在运行，请等待完成或先停止任务；其他工作区仍可并行运行",
        )

    if body.skill != "auto":
        try:
            skills.match_enabled(body.skill)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail="指定的 Skill 不存在或已停用") from exc

    parent: RunRecord | None = None
    continuation: dict[str, object] | None = None
    requested_session = sessions.get(body.session_id) if body.session_id else None
    if body.session_id and not requested_session:
        raise HTTPException(status_code=404, detail="指定的 Session 不存在")
    if body.parent_entry_id:
        if not requested_session:
            raise HTTPException(status_code=400, detail="从历史节点分支时必须提供 Session")
        parent_entry = requested_session.entries.get(body.parent_entry_id)
        if not parent_entry:
            raise HTTPException(status_code=404, detail="要分支的 Session 节点不存在")
        parent = runs.get(parent_entry.run_id)
        if not parent:
            raise HTTPException(status_code=404, detail="历史节点对应的任务不存在")
    elif body.parent_run_id:
        parent = runs.get(body.parent_run_id)
        if not parent:
            raise HTTPException(status_code=404, detail="要继续的历史任务不存在")

    if parent:
        if parent.workspace != normalized_workspace:
            raise HTTPException(status_code=400, detail="只能在原工作区继续历史任务")
        if parent.status not in {"completed", "failed", "cancelled"}:
            raise HTTPException(status_code=409, detail="原任务仍在运行，请先等待完成或停止任务")
        continuation = _continuation_context(parent)

    run_id = f"run_{uuid.uuid4().hex[:12]}"
    session = requested_session
    if parent and not session:
        session = sessions.get(parent.session_id) if parent.session_id else None
    if not session:
        session = sessions.create(normalized_workspace, parent.root_task if parent else body.task.strip())
    if session.workspace != normalized_workspace:
        raise HTTPException(status_code=400, detail="Session 与当前工作区不匹配")
    parent_entry_id = body.parent_entry_id or (parent.session_entry_id if parent else None)
    session_entry = sessions.create_entry(
        session.session_id,
        run_id=run_id,
        task=body.task.strip(),
        root_task=parent.root_task if parent else body.task.strip(),
        parent_id=parent_entry_id,
    )
    record = RunRecord(
        run_id=run_id,
        task=body.task.strip(),
        workspace=normalized_workspace,
        requested_skill=body.skill,
        root_task=parent.root_task if parent else body.task.strip(),
        parent_run_id=parent.run_id if parent else None,
        continuation_context=continuation,
        session_id=session.session_id,
        session_entry_id=session_entry.entry_id,
        parent_entry_id=parent_entry_id,
    )
    runs.add(record)
    events.create(run_id)
    record.background_task = asyncio.create_task(agent.run(record, workspace), name=run_id)
    return {
        "run_id": run_id,
        "status": record.status,
        "mode": model.mode_name,
        "session_id": session.session_id,
        "session_entry_id": session_entry.entry_id,
    }


@app.get("/api/runs")
async def list_runs() -> dict[str, object]:
    items: list[dict[str, object]] = []
    for record in runs.list_recent():
        channel = events.get(record.run_id)
        run_events = channel.events if channel else []
        plan_event = next(
            (
                event for event in reversed(run_events)
                if event.get("type") == "plan_updated" and isinstance(event.get("payload", {}).get("items"), list)
            ),
            None,
        )
        plan_items = plan_event["payload"]["items"] if plan_event else []
        completed_steps = sum(item.get("status") == "success" for item in plan_items if isinstance(item, dict))
        last_event = run_events[-1] if run_events else None
        items.append({
            "run_id": record.run_id,
            "task": record.root_task,
            "latest_message": record.task,
            "workspace": record.workspace,
            "status": record.status,
            "phase": record.phase,
            "summary": record.summary,
            "created_at": record.created_at,
            "completed_steps": completed_steps,
            "total_steps": len(plan_items),
            "changed_files": len(record.changed_files),
            "last_event": {
                "type": last_event.get("type"),
                "title": last_event.get("title"),
                "summary": last_event.get("summary"),
                "timestamp": last_event.get("timestamp"),
            } if last_event else None,
        })
    attention_statuses = {"waiting_skill_confirmation", "waiting_approval", "waiting_interaction_confirmation"}
    return {
        "runs": items,
        "active": sum(str(item["status"]) in {"created", "running", *attention_statuses} for item in items),
        "attention": sum(str(item["status"]) in attention_statuses for item in items),
    }


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, object]:
    record = runs.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="任务不存在")
    channel = events.get(run_id)
    return {
        **record.public_dict(),
        "events": channel.events if channel else [],
        "conversation": _conversation_chain(record),
        "session_tree": sessions.public_tree(record.session_id, record.session_entry_id) if record.session_id else None,
    }


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str, active_entry_id: str | None = Query(default=None)) -> dict[str, object]:
    tree = sessions.public_tree(session_id, active_entry_id)
    if not tree:
        raise HTTPException(status_code=404, detail="Session 不存在")
    return tree


@app.get("/api/runs/{run_id}/events")
async def stream_events(run_id: str, request: Request) -> StreamingResponse:
    if not runs.get(run_id) or not events.get(run_id):
        raise HTTPException(status_code=404, detail="任务不存在")

    async def generate() -> AsyncIterator[str]:
        cursor = 0
        while True:
            if await request.is_disconnected():
                return
            try:
                batch, terminal = await asyncio.wait_for(events.wait_for_events(run_id, cursor), timeout=15)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            for event in batch:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                cursor += 1
            if terminal:
                return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, str]:
    record = runs.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="任务不存在")
    if record.status not in {"completed", "failed", "cancelled"}:
        record.cancel_requested = True
        if record.pending_skill_selection:
            record.resolve_skill_confirmation(
                str(record.pending_skill_selection["selection_id"]), "cancelled",
            )
        if record.pending_approval:
            record.resolve_approval(str(record.pending_approval["approval_id"]), "cancelled")
        if record.pending_interaction:
            record.resolve_interaction_confirmation(
                str(record.pending_interaction["model_id"]), "cancelled",
            )
    active = record.status in {"created", "running", "waiting_skill_confirmation", "waiting_approval", "waiting_interaction_confirmation"}
    return {"run_id": run_id, "status": "cancelling" if active else record.status}


@app.post("/api/runs/{run_id}/steering", status_code=202)
async def steer_run(run_id: str, body: SteeringRequest) -> dict[str, str]:
    record = runs.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="任务不存在")
    if record.status != "running":
        if record.status in {"waiting_skill_confirmation", "waiting_approval", "waiting_interaction_confirmation"}:
            raise HTTPException(status_code=409, detail="当前任务正在等待专门确认，请先处理页面中的确认选项")
        raise HTTPException(status_code=409, detail="只有正在运行的任务可以接收 Steering 消息")
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Steering 消息不能为空")
    try:
        item = record.enqueue_steering(message)
    except ValueError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    if record.session_id and record.session_entry_id:
        sessions.append_steering(record.session_id, record.session_entry_id, item)
    await events.emit(
        run_id,
        "steering_received",
        record.phase,
        "info",
        "收到方向修正",
        message,
        {
            "steering_id": item["steering_id"],
            "message": message,
            "status": "queued",
            "applies_at": "next_model_turn",
        },
    )
    return {
        "run_id": run_id,
        "steering_id": str(item["steering_id"]),
        "status": "queued",
    }


@app.post("/api/runs/{run_id}/approvals/{approval_id}")
async def resolve_approval(run_id: str, approval_id: str, body: ApprovalDecisionRequest) -> dict[str, str]:
    record = runs.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="任务不存在")
    if record.status != "waiting_approval" or not record.pending_approval:
        raise HTTPException(status_code=409, detail="当前任务没有等待处理的授权请求")
    if not record.resolve_approval(approval_id, body.decision):
        raise HTTPException(status_code=409, detail="授权请求已失效或不属于当前任务")
    return {"run_id": run_id, "approval_id": approval_id, "decision": body.decision}


@app.post("/api/runs/{run_id}/skill-selection/{selection_id}")
async def resolve_skill_selection(
    run_id: str,
    selection_id: str,
    body: SkillSelectionDecisionRequest,
) -> dict[str, str]:
    record = runs.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="任务不存在")
    if record.status != "waiting_skill_confirmation" or not record.pending_skill_selection:
        raise HTTPException(status_code=409, detail="当前任务没有等待确认的 Skill 候选")
    if not record.resolve_skill_confirmation(selection_id, body.skill_name):
        raise HTTPException(status_code=409, detail="Skill 候选已失效或选择无效")
    return {"run_id": run_id, "selection_id": selection_id, "skill_name": body.skill_name}


@app.post("/api/runs/{run_id}/interaction/{model_id}")
async def resolve_interaction_confirmation(
    run_id: str,
    model_id: str,
    body: InteractionDecisionRequest,
) -> dict[str, str]:
    record = runs.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="任务不存在")
    if record.status != "waiting_interaction_confirmation" or not record.pending_interaction:
        raise HTTPException(status_code=409, detail="当前任务没有等待确认的交互流程")
    feedback = body.feedback.strip()
    if body.decision == "revise" and not feedback:
        raise HTTPException(status_code=400, detail="请说明需要如何调整交互流程")
    if not record.resolve_interaction_confirmation(model_id, body.decision, feedback):
        raise HTTPException(status_code=409, detail="交互流程确认请求已失效或不属于当前任务")
    return {"run_id": run_id, "model_id": model_id, "decision": body.decision}


@app.post("/api/demo/reset")
async def reset_demo(workspace: str = "calculator") -> dict[str, str]:
    if runs.has_active_workspace(workspace):
        raise HTTPException(status_code=409, detail="该工作区仍有 Agent 任务运行，请先停止任务再重置")
    if workspace == "calculator":
        demo_workspace = resolve_workspace(WORKSPACE_ROOT, workspace)
        source = demo_workspace / "src" / "calculator.py"
        source.write_text(DEMO_BUGGY_SOURCE, encoding="utf-8")
    elif workspace == "star-catcher":
        demo_workspace = resolve_workspace(WORKSPACE_ROOT, workspace)
        source = demo_workspace / "src" / "game.js"
        content = source.read_text(encoding="utf-8")
        if STAR_CATCHER_FIXED_BLOCK in content:
            source.write_text(content.replace(STAR_CATCHER_FIXED_BLOCK, STAR_CATCHER_BUGGY_BLOCK, 1), encoding="utf-8")
        elif STAR_CATCHER_BUGGY_BLOCK not in content:
            raise HTTPException(status_code=409, detail="游戏逻辑已发生其他修改，无法安全恢复故障")
    elif workspace == "2048-game":
        demo_workspace = resolve_workspace(WORKSPACE_ROOT, workspace)
        requirements = demo_workspace / "REQUIREMENTS.md"
        if not requirements.is_file():
            raise HTTPException(status_code=409, detail="2048 需求文档不存在，无法安全重置")
        generated_entries = [entry for entry in demo_workspace.iterdir() if entry.name != requirements.name]
        if generated_entries:
            backup_id = f"2048-{uuid.uuid4().hex[:10]}"
            backup_root = RUNTIME_HOME / "reset-backups" / backup_id
            backup_root.mkdir(parents=True, exist_ok=False)
            for entry in generated_entries:
                shutil.move(str(entry), str(backup_root / entry.name))
    elif workspace == "approval-demo":
        demo_workspace = resolve_workspace(WORKSPACE_ROOT, workspace)
        fixture = PROJECT_ROOT / "fixtures" / "approval-demo"
        if not fixture.is_dir():
            raise HTTPException(status_code=409, detail="Approval Demo 基线不存在，无法安全重置")
        backup_root = RUNTIME_HOME / "reset-backups" / f"approval-demo-{uuid.uuid4().hex[:10]}"
        backup_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(demo_workspace), str(backup_root))
        shutil.copytree(fixture, demo_workspace)
    elif workspace == "order-engine-lab":
        demo_workspace = resolve_workspace(WORKSPACE_ROOT, workspace)
        fixture = PROJECT_ROOT / "fixtures" / "order-engine-lab"
        if not fixture.is_dir():
            raise HTTPException(status_code=409, detail="Failure Lab 基线不存在，无法安全重置")
        backup_root = RUNTIME_HOME / "reset-backups" / f"order-lab-{uuid.uuid4().hex[:10]}"
        backup_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(demo_workspace), str(backup_root))
        shutil.copytree(fixture, demo_workspace)
    else:
        raise HTTPException(status_code=400, detail="仅支持重置内置演示项目")
    return {"status": "reset", "workspace": workspace}


DEMO_BUGGY_SOURCE = '''"""Small calculator used by the IntentFlow demo."""


def add(a: float, b: float) -> float:
    """Return the sum of two numbers."""
    return a - b  # BUG: addition should use +


def subtract(a: float, b: float) -> float:
    """Return the difference of two numbers."""
    return a - b
'''

STAR_CATCHER_BUGGY_BLOCK = '''    score: state.score + gainedScore,
    combo: 0,
    bestCombo: Math.max(state.bestCombo, nextCombo),
'''

STAR_CATCHER_FIXED_BLOCK = '''    score: state.score + gainedScore,
    combo: nextCombo,
    bestCombo: Math.max(state.bestCombo, nextCombo),
'''
