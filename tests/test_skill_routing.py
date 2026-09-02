import asyncio
import json
import uuid
from pathlib import Path

from backend.agent.loop import AgentLoop, _should_create_interaction_model
from backend.events.store import EventStore
from backend.llm.client import ModelTurn, ToolCall
from backend.skills.router import SkillRouter
from backend.state import RunRecord


SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"


def test_flowchart_decision_is_independent_from_skill_keywords(tmp_path: Path) -> None:
    router = SkillRouter(SKILLS_ROOT)
    frontend = next(skill for skill in router.skills if skill.name == "frontend_build")
    record = RunRecord("run_flow", "请先生成这个产品的终端用户交互流程图", ".")

    enabled, reason_code, _reason = _should_create_interaction_model(record, tmp_path)

    assert "流程图" not in frontend.keywords
    assert enabled is True
    assert reason_code == "explicit_request"


class SemanticRoutingModel:
    mode_name = "model"
    provider_name = "test"
    model_name = "semantic-router"

    def __init__(self, *, selected: str | list[str], confidence: float) -> None:
        self.selected = [selected] if isinstance(selected, str) else selected
        self.confidence = confidence

    async def complete(self, messages, tools) -> ModelTurn:
        tool_names = {tool["function"]["name"] for tool in tools}
        if "select_skills" in tool_names:
            return self._call("select_skills", {
                "skill_names": self.selected,
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
    assert selected["payload"]["skills"] == ["documentation"]
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
    assert selected["payload"]["skills"] == ["test_writer"]
    assert selected["payload"]["strategy"] == "semantic"
    assert selected["payload"]["confidence"] == 0.91
    assert not any(event["type"] == "skill_confirmation_requested" for event in events.get("run_semantic").events)


def test_low_confidence_route_continues_automatically(tmp_path: Path) -> None:
    events = EventStore(tmp_path / "events")
    events.create("run_auto_low_confidence")
    record = RunRecord("run_auto_low_confidence", "检查这个项目并提高质量", ".")
    loop = AgentLoop(
        events,
        SkillRouter(SKILLS_ROOT),
        SemanticRoutingModel(selected="bug_fix", confidence=0.42),
        max_steps=3,
    )

    asyncio.run(loop.run(record, tmp_path))

    selected = next(
        event for event in events.get("run_auto_low_confidence").events
        if event["type"] == "skill_selected"
    )
    assert record.status == "completed"
    assert selected["payload"]["skill"] == "bug_fix"
    assert selected["payload"]["skills"] == ["bug_fix"]
    assert selected["payload"]["strategy"] == "semantic"
    assert selected["payload"]["confidence"] == 0.42
    assert not any(
        event["type"] == "skill_confirmation_requested"
        for event in events.get("run_auto_low_confidence").events
    )


def test_semantic_router_can_combine_complementary_skills(tmp_path: Path) -> None:
    events = EventStore(tmp_path / "events")
    events.create("run_multi")
    record = RunRecord("run_multi", "修复费用计算错误并补充边界单元测试", ".")
    loop = AgentLoop(
        events,
        SkillRouter(SKILLS_ROOT),
        SemanticRoutingModel(selected=["bug_fix", "test_writer"], confidence=0.94),
        max_steps=3,
    )

    asyncio.run(loop.run(record, tmp_path))

    selected = next(event for event in events.get("run_multi").events if event["type"] == "skill_selected")
    assert record.status == "completed"
    assert selected["payload"]["skills"] == ["bug_fix", "test_writer"]
    assert selected["title"] == "Bug Fix Skill + Test Writer Skill"
    assert selected["payload"]["strategy"] == "semantic"


def test_confirmed_interaction_model_guides_vague_skill_request(tmp_path: Path) -> None:
    events = EventStore(tmp_path / "events")
    events.create("run_vague")
    model = SemanticRoutingModel(selected="bug_fix", confidence=0.2)
    model.mode_name = "demo"
    loop = AgentLoop(events, SkillRouter(SKILLS_ROOT), model, max_steps=3)
    record = RunRecord("run_vague", "继续完成这个项目", ".")

    selected = asyncio.run(loop._select_skills(
        record,
        interaction_model={"title": "知识卡片网页", "summary": "用户记录并复习知识卡片"},
    ))

    assert selected is not None
    assert selected[0].skill.name == "frontend_build"
