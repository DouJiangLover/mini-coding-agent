import asyncio
import json
import uuid
from pathlib import Path

from backend.agent.loop import AgentLoop
from backend.events.store import EventStore
from backend.llm.client import ModelTurn, ToolCall
from backend.skills.router import SkillRouter
from backend.state import RunRecord


SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"


class RepeatingBlockedCommandModel:
    mode_name = "failure-test"
    provider_name = "test"
    model_name = "repeating-blocked-command"

    async def complete(self, messages, tools) -> ModelTurn:
        call_id = f"failure_{uuid.uuid4().hex[:8]}"
        arguments = {"command": "python -rf"}
        raw_call = {
            "id": call_id,
            "type": "function",
            "function": {"name": "run_command", "arguments": json.dumps(arguments)},
        }
        return ModelTurn(
            assistant_message={"role": "assistant", "content": None, "tool_calls": [raw_call]},
            tool_calls=[ToolCall(call_id=call_id, name="run_command", arguments=arguments)],
        )


def test_agent_stops_after_three_consecutive_tool_failures(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    events = EventStore(tmp_path / "events")
    events.create("run_failure")
    record = RunRecord("run_failure", "修复复杂失败测试", ".")
    loop = AgentLoop(events, SkillRouter(SKILLS_ROOT), RepeatingBlockedCommandModel(), max_steps=8)

    asyncio.run(loop.run(record, tmp_path))

    channel = events.get("run_failure")
    assert record.status == "failed"
    assert record.summary == "连续三次工具执行失败，Agent 已停止以避免无效循环。"
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert channel is not None and channel.terminal
    assert len([event for event in channel.events if event["type"] == "error"]) == 3
    assert channel.events[-1]["type"] == "run_finished"
    assert channel.events[-1]["payload"]["status"] == "failed"


class ApprovalFlowModel:
    mode_name = "approval-test"
    provider_name = "test"
    model_name = "approval-flow"

    def __init__(self) -> None:
        self.turn = 0

    async def complete(self, messages, tools) -> ModelTurn:
        self.turn += 1
        if self.turn == 1:
            name = "list_files"
            arguments = {"path": ".", "max_depth": 2}
        elif self.turn == 2:
            name = "run_command"
            arguments = {"command": "pytest -q", "timeout": 30}
        elif self.turn == 3:
            name = "create_file"
            arguments = {"path": "src/audit.py", "content": "def record(event):\n    return event\n"}
        elif self.turn == 4:
            name = "run_command"
            arguments = {"command": "python3 -m py_compile src/audit.py", "timeout": 30}
        elif self.turn == 5:
            name = "finish"
            arguments = {"summary": "已在用户授权后创建审计模块", "verification": "语法检查通过"}
        elif self.turn == 6:
            name = "read_file"
            arguments = {"path": "src/audit.py", "start_line": 1, "end_line": 80}
        else:
            name = "finish"
            arguments = {"summary": "已在用户授权后创建并自检审计模块", "verification": "授权动作只执行一次"}
        call_id = f"approval_{uuid.uuid4().hex[:8]}"
        raw_call = {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }
        return ModelTurn(
            assistant_message={"role": "assistant", "content": None, "tool_calls": [raw_call]},
            tool_calls=[ToolCall(call_id=call_id, name=name, arguments=arguments)],
        )


def test_agent_waits_for_single_action_approval_then_continues(tmp_path: Path) -> None:
    async def scenario() -> tuple[RunRecord, EventStore]:
        events = EventStore(tmp_path / "events")
        events.create("run_approval")
        record = RunRecord("run_approval", "修复失败测试并创建缺失模块", ".")
        loop = AgentLoop(events, SkillRouter(SKILLS_ROOT), ApprovalFlowModel(), max_steps=9)
        task = asyncio.create_task(loop.run(record, tmp_path))

        for _ in range(100):
            if record.status == "waiting_approval":
                break
            await asyncio.sleep(0.01)

        assert record.status == "waiting_approval"
        assert record.pending_approval is not None
        assert record.pending_approval["tool"] == "create_file"
        assert not (tmp_path / "src" / "audit.py").exists()
        assert record.resolve_approval(record.pending_approval["approval_id"], "allow")

        await asyncio.wait_for(task, timeout=2)
        return record, events

    record, events = asyncio.run(scenario())
    channel = events.get("run_approval")

    assert record.status == "completed"
    assert (tmp_path / "src" / "audit.py").is_file()
    assert channel is not None
    assert [event["type"] for event in channel.events].count("approval_requested") == 1
    assert [event["type"] for event in channel.events].count("approval_resolved") == 1
    resolved = next(event for event in channel.events if event["type"] == "approval_resolved")
    assert resolved["payload"]["decision"] == "allow"


def test_agent_continues_after_user_denies_permission(tmp_path: Path) -> None:
    async def scenario() -> RunRecord:
        events = EventStore(tmp_path / "events-denied")
        events.create("run_denied")
        record = RunRecord("run_denied", "修复失败测试并创建缺失模块", ".")
        loop = AgentLoop(events, SkillRouter(SKILLS_ROOT), ApprovalFlowModel(), max_steps=9)
        task = asyncio.create_task(loop.run(record, tmp_path))

        for _ in range(100):
            if record.status == "waiting_approval":
                break
            await asyncio.sleep(0.01)

        assert record.pending_approval is not None
        assert record.resolve_approval(record.pending_approval["approval_id"], "deny")
        await asyncio.wait_for(task, timeout=2)
        return record

    record = asyncio.run(scenario())

    assert record.status == "completed"
    assert not (tmp_path / "src" / "audit.py").exists()
