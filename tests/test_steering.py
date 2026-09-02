import asyncio
import json
import uuid
from pathlib import Path

from backend.agent.loop import AgentLoop
from backend.events.store import EventStore
from backend.llm.client import ModelTurn, ToolCall
from backend.sessions.store import SessionStore
from backend.skills.router import SkillRouter
from backend.state import RunRecord


SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"


class SteeringAwareModel:
    mode_name = "steering-test"
    provider_name = "test"
    model_name = "steering-aware"

    def __init__(self) -> None:
        self.first_request_started = asyncio.Event()
        self.release_first_request = asyncio.Event()
        self.turn = 0
        self.saw_steering = False

    async def complete(self, messages, tools) -> ModelTurn:
        self.turn += 1
        if self.turn == 1:
            self.first_request_started.set()
            await self.release_first_request.wait()
            return self._call("list_files", {"path": ".", "max_depth": 1})

        context = "\n".join(str(message.get("content") or "") for message in messages)
        self.saw_steering = "不要修改文件，只总结项目结构" in context
        return self._call("finish", {
            "summary": "已根据运行中的方向修正停止修改并完成检查",
            "verification": "已读取目录结构",
        })

    @staticmethod
    def _call(name: str, arguments: dict) -> ModelTurn:
        call_id = f"steering_{uuid.uuid4().hex[:8]}"
        raw = {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
        }
        return ModelTurn(
            assistant_message={"role": "assistant", "content": None, "tool_calls": [raw]},
            tool_calls=[ToolCall(call_id=call_id, name=name, arguments=arguments)],
        )


def test_steering_sent_during_model_request_is_applied_before_next_decision(tmp_path: Path) -> None:
    async def scenario() -> tuple[RunRecord, EventStore, SessionStore, SteeringAwareModel, str]:
        event_store = EventStore(tmp_path / "events")
        event_store.create("run_steering")
        session_store = SessionStore(tmp_path / "sessions")
        session = session_store.create(".", "分析项目")
        entry = session_store.create_entry(
            session.session_id,
            run_id="run_steering",
            task="分析项目",
            root_task="分析项目",
        )
        record = RunRecord(
            "run_steering",
            "分析当前项目结构",
            ".",
            session_id=session.session_id,
            session_entry_id=entry.entry_id,
        )
        model = SteeringAwareModel()
        loop = AgentLoop(
            event_store,
            SkillRouter(SKILLS_ROOT),
            model,
            max_steps=4,
            session_store=session_store,
        )
        task = asyncio.create_task(loop.run(record, tmp_path))
        await asyncio.wait_for(model.first_request_started.wait(), timeout=1)

        steering = record.enqueue_steering("不要修改文件，只总结项目结构")
        session_store.append_steering(session.session_id, entry.entry_id, steering)
        await event_store.emit(
            record.run_id,
            "steering_received",
            record.phase,
            "info",
            "收到方向修正",
            steering["message"],
            steering,
        )
        model.release_first_request.set()
        await asyncio.wait_for(task, timeout=2)
        return record, event_store, session_store, model, session.session_id

    record, event_store, session_store, model, session_id = asyncio.run(scenario())

    assert record.status == "completed"
    assert model.saw_steering
    assert record.steering_messages[0]["status"] == "applied"
    event_types = [event["type"] for event in event_store.get("run_steering").events]
    assert event_types.index("steering_received") < event_types.index("steering_applied")

    restored = SessionStore(session_store.root)
    tree = restored.public_tree(session_id)
    assert tree is not None
    messages = tree["entries"][0]["steering_messages"]
    assert messages[0]["message"] == "不要修改文件，只总结项目结构"
    assert messages[0]["status"] == "applied"
