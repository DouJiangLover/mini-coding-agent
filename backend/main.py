from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from backend.agent.loop import AgentLoop
from backend.events.store import EventStore
from backend.llm.client import create_model_client
from backend.skills.router import SkillRouter
from backend.state import RunRecord, RunStore
from backend.workspace.guard import WorkspaceViolation, resolve_workspace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
workspace_setting = Path(os.getenv("TRACE_WORKSPACE_ROOT", "."))
WORKSPACE_ROOT = (workspace_setting if workspace_setting.is_absolute() else PROJECT_ROOT / workspace_setting).resolve()
RUNTIME_ROOT = PROJECT_ROOT / ".tracecoder" / "runs"

events = EventStore(RUNTIME_ROOT)
runs = RunStore()
model = create_model_client()
skills = SkillRouter(PROJECT_ROOT / "skills")
agent = AgentLoop(events, skills, model)

app = FastAPI(
    title="TraceCoder API",
    description="A framework-free local coding agent core.",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv("TRACECODER_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000").split(",")],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class CreateRunRequest(BaseModel):
    task: str = Field(min_length=3, max_length=8_000)
    workspace: str = Field(default=".", min_length=1, max_length=500)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "mode": model.mode_name}


@app.post("/api/runs", status_code=202)
async def create_run(body: CreateRunRequest) -> dict[str, str]:
    try:
        workspace = resolve_workspace(WORKSPACE_ROOT, body.workspace)
    except WorkspaceViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    run_id = f"run_{uuid.uuid4().hex[:12]}"
    record = RunRecord(run_id=run_id, task=body.task.strip(), workspace=body.workspace)
    runs.add(record)
    events.create(run_id)
    record.background_task = asyncio.create_task(agent.run(record, workspace), name=run_id)
    return {"run_id": run_id, "status": record.status, "mode": model.mode_name}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, object]:
    record = runs.get(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="任务不存在")
    channel = events.get(run_id)
    return {**record.public_dict(), "events": channel.events if channel else []}


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
    return {"run_id": run_id, "status": "cancelling" if record.status == "running" else record.status}


@app.post("/api/demo/reset")
async def reset_demo() -> dict[str, str]:
    demo_workspace = resolve_workspace(PROJECT_ROOT, "examples/calculator")
    source = demo_workspace / "src" / "calculator.py"
    source.write_text(DEMO_BUGGY_SOURCE, encoding="utf-8")
    return {"status": "reset", "workspace": "examples/calculator"}


DEMO_BUGGY_SOURCE = '''"""Small calculator used by the TraceCoder demo."""


def add(a: float, b: float) -> float:
    """Return the sum of two numbers."""
    return a - b  # BUG: addition should use +


def subtract(a: float, b: float) -> float:
    """Return the difference of two numbers."""
    return a - b
'''
