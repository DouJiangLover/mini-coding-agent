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


class InteractionFlowModel:
    mode_name = "interaction-test"
    provider_name = "test"
    model_name = "interaction-flow"

    def __init__(self) -> None:
        self.interaction_calls = 0
        self.saw_confirmed_context = False

    async def complete(self, messages, tools) -> ModelTurn:
        tool_names = {tool["function"]["name"] for tool in tools}
        if "submit_interaction_model" in tool_names:
            self.interaction_calls += 1
            context = "\n".join(str(message.get("content") or "") for message in messages)
            revised = "增加设置页" in context
            pages = [
                {"id": "home", "name": "首页", "purpose": "设置专注时间并开始"},
                {"id": "timer", "name": "计时页", "purpose": "展示倒计时和控制按钮"},
            ]
            flows = [
                {"from": "home", "action": "点击开始", "to": "timer"},
                {"from": "timer", "action": "完成或放弃", "to": "home"},
            ]
            if revised:
                pages.append({"id": "settings", "name": "设置页", "purpose": "调整提示音和默认时长"})
                flows.append({"from": "home", "action": "打开设置", "to": "settings"})
                flows.append({"from": "settings", "action": "保存设置", "to": "home"})
            return self._call("submit_interaction_model", {
                "title": "番茄钟",
                "summary": "用户设置专注时长，完成计时后回到首页。",
                "pages": pages,
                "flows": flows,
                "states": [
                    {"from": "idle", "event": "开始", "to": "running"},
                    {"from": "running", "event": "暂停", "to": "paused"},
                    {"from": "paused", "event": "继续", "to": "running"},
                    {"from": "running", "event": "倒计时结束", "to": "completed"},
                ],
                "acceptance_criteria": ["可以开始、暂停和继续计时", "完成后有明确提示"],
            })

        self.saw_confirmed_context = any(
            "用户已经确认以下产品交互模型" in str(message.get("content") or "")
            for message in messages
        )
        return self._call("finish", {"summary": "已按确认的交互模型完成任务", "verification": "流程已确认"})

    @staticmethod
    def _call(name: str, arguments: dict) -> ModelTurn:
        call_id = f"interaction_{uuid.uuid4().hex[:8]}"
        raw_call = {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
        }
        return ModelTurn(
            assistant_message={"role": "assistant", "content": None, "tool_calls": [raw_call]},
            tool_calls=[ToolCall(call_id=call_id, name=name, arguments=arguments)],
        )


async def wait_for_interaction(record: RunRecord, previous_id: str = "") -> dict:
    for _ in range(200):
        pending = record.pending_interaction
        if record.status == "waiting_interaction_confirmation" and pending and pending["model_id"] != previous_id:
            return pending
        await asyncio.sleep(0.01)
    raise AssertionError("Agent 没有进入交互流程确认状态")


def test_agent_waits_for_interaction_confirmation_before_implementation(tmp_path: Path) -> None:
    async def scenario() -> tuple[RunRecord, EventStore, InteractionFlowModel]:
        requirements = tmp_path / "REQUIREMENTS.md"
        requirements.write_text("# 番茄钟需求\n支持开始、暂停和完成。\n", encoding="utf-8")
        events = EventStore(tmp_path / "events")
        events.create("run_interaction")
        record = RunRecord("run_interaction", "请按照需求文档从零实现一个番茄钟小程序", ".")
        model = InteractionFlowModel()
        loop = AgentLoop(events, SkillRouter(SKILLS_ROOT), model, max_steps=3)
        task = asyncio.create_task(loop.run(record, tmp_path))

        pending = await wait_for_interaction(record)
        assert pending["title"] == "番茄钟"
        assert {path.name for path in tmp_path.iterdir()} == {"REQUIREMENTS.md", "events"}
        assert record.changed_files == []
        assert record.resolve_interaction_confirmation(pending["model_id"], "approve")

        await asyncio.wait_for(task, timeout=2)
        return record, events, model

    record, events, model = asyncio.run(scenario())
    channel = events.get("run_interaction")

    assert record.status == "completed"
    assert model.saw_confirmed_context
    assert channel is not None
    event_types = [event["type"] for event in channel.events]
    assert "interaction_model_created" in event_types
    assert "interaction_confirmation_requested" in event_types
    assert "interaction_confirmation_resolved" in event_types


def test_user_feedback_regenerates_interaction_model(tmp_path: Path) -> None:
    async def scenario() -> tuple[RunRecord, EventStore, InteractionFlowModel]:
        (tmp_path / "REQUIREMENTS.md").write_text("# 番茄钟需求\n", encoding="utf-8")
        events = EventStore(tmp_path / "events")
        events.create("run_revision")
        record = RunRecord("run_revision", "根据需求文档从零制作番茄钟前端页面", ".")
        model = InteractionFlowModel()
        loop = AgentLoop(events, SkillRouter(SKILLS_ROOT), model, max_steps=3)
        task = asyncio.create_task(loop.run(record, tmp_path))

        first = await wait_for_interaction(record)
        assert record.resolve_interaction_confirmation(first["model_id"], "revise", "增加设置页")
        second = await wait_for_interaction(record, first["model_id"])
        assert second["revision"] == 2
        assert any(page["id"] == "settings" for page in second["pages"])
        assert record.changed_files == []
        assert record.resolve_interaction_confirmation(second["model_id"], "approve")

        await asyncio.wait_for(task, timeout=2)
        return record, events, model

    record, events, model = asyncio.run(scenario())
    channel = events.get("run_revision")

    assert record.status == "completed"
    assert model.interaction_calls == 2
    assert channel is not None
    assert [event["type"] for event in channel.events].count("interaction_model_created") == 2
    decisions = [
        event["payload"]["decision"]
        for event in channel.events
        if event["type"] == "interaction_confirmation_resolved"
    ]
    assert decisions == ["revise", "approve"]
