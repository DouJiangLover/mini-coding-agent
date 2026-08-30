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


class SemanticRoutingModel:
    mode_name = "model"
    provider_name = "test"
    model_name = "semantic-router"

    def __init__(self, *, selected: str, confidence: float) -> None:
        self.selected = selected
        self.confidence = confidence

    async def complete(self, messages, tools) -> ModelTurn:
        tool_names = {tool["function"]["name"] for tool in tools}
        if "select_skill" in tool_names:
            return self._call("select_skill", {
                "skill_name": self.selected,
                "confidence": self.confidence,
                "reason": "任务重点是补充可验证的测试覆盖。",
            })
        history = [message for message in messages if message.get("role") == "tool"]
        if not history:
            return self._call("list_files", {"path": ".", "max_depth": 2})
        return self._call("finish", {"summary": "路由测试完成", "verification": "已检查工作区"})

    @staticmethod
    def _call(name: str, arguments: dict) -> ModelTurn:
        call_id = f"route_{uuid.uuid4().hex[:8]}"
        raw_call = {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
        }
        return ModelTurn(
            assistant_message={"role": "assistant", "content": None, "tool_calls": [raw_call]},
            tool_calls=[ToolCall(call_id=call_id, name=name, arguments=arguments)],
        )


def test_manual_skill_selection_overrides_task_keywords(tmp_path: Path) -> None:
    events = EventStore(tmp_path / "events")
    events.create("run_manual")
    record = RunRecord(
        "run_manual",
        "请修复失败测试",
        ".",
        requested_skill="documentation",
    )
    loop = AgentLoop(
        events,
        SkillRouter(SKILLS_ROOT),
        SemanticRoutingModel(selected="bug_fix", confidence=0.99),
        max_steps=3,
    )

    asyncio.run(loop.run(record, tmp_path))

    selected = next(event for event in events.get("run_manual").events if event["type"] == "skill_selected")
    assert selected["payload"]["skill"] == "documentation"
    assert selected["payload"]["strategy"] == "manual"
    assert selected["payload"]["confidence"] == 1.0


def test_high_confidence_semantic_route_continues_automatically(tmp_path: Path) -> None:
    events = EventStore(tmp_path / "events")
    events.create("run_semantic")
    record = RunRecord("run_semantic", "检查核心行为并增加边界覆盖", ".")
    loop = AgentLoop(
        events,
        SkillRouter(SKILLS_ROOT),
        SemanticRoutingModel(selected="test_writer", confidence=0.91),
        max_steps=3,
    )

    asyncio.run(loop.run(record, tmp_path))

    selected = next(event for event in events.get("run_semantic").events if event["type"] == "skill_selected")
    assert record.status == "completed"
    assert selected["payload"]["skill"] == "test_writer"
    assert selected["payload"]["strategy"] == "semantic"
    assert selected["payload"]["confidence"] == 0.91
    assert not any(event["type"] == "skill_confirmation_requested" for event in events.get("run_semantic").events)


def test_low_confidence_route_waits_for_user_choice(tmp_path: Path) -> None:
    async def scenario() -> tuple[RunRecord, EventStore]:
        events = EventStore(tmp_path / "events")
        events.create("run_confirm")
        record = RunRecord("run_confirm", "检查这个项目并提高质量", ".")
        loop = AgentLoop(
            events,
            SkillRouter(SKILLS_ROOT),
            SemanticRoutingModel(selected="bug_fix", confidence=0.42),
            max_steps=3,
        )
        task = asyncio.create_task(loop.run(record, tmp_path))

        for _ in range(200):
            if record.status == "waiting_skill_confirmation" and record.pending_skill_selection:
                break
            await asyncio.sleep(0.01)

        assert record.pending_skill_selection is not None
        selection = record.pending_skill_selection
        candidate_names = {item["name"] for item in selection["candidates"]}
        assert "test_writer" in candidate_names
        assert record.resolve_skill_confirmation(selection["selection_id"], "test_writer")
        await asyncio.wait_for(task, timeout=2)
        return record, events

    record, events = asyncio.run(scenario())
    selected = next(event for event in events.get("run_confirm").events if event["type"] == "skill_selected")

    assert record.status == "completed"
    assert selected["payload"]["skill"] == "test_writer"
    assert selected["payload"]["strategy"] == "user_confirmed"
    assert any(event["type"] == "skill_confirmation_requested" for event in events.get("run_confirm").events)
    assert any(event["type"] == "skill_confirmation_resolved" for event in events.get("run_confirm").events)
