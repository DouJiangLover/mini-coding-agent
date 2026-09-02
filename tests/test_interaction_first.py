import asyncio
import json
import uuid
from pathlib import Path

from backend.agent.loop import AgentLoop, _normalize_interaction_model, _should_create_interaction_model
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
        history = [message for message in messages if message.get("role") == "tool"]
        requirement_ids = ["AC-01", "AC-02"]
        if not history:
            return self._call("list_files", {"path": ".", "max_depth": 2})
        last = history[-1]
        try:
            result = json.loads(last.get("content") or "{}")
        except json.JSONDecodeError:
            result = {}
        if last.get("name") == "list_files":
            return self._call("create_file", {
                "path": "index.html",
                "content": (
                    "<!doctype html><title>番茄钟</title>"
                    "<button>开始</button><button>暂停</button><button>继续</button>"
                    "<p>专注完成</p>"
                ),
                "requirement_ids": requirement_ids,
            })
        if last.get("name") == "create_file":
            path = str(result.get("data", {}).get("path", ""))
            if path == "index.html":
                return self._call("create_file", {
                    "path": "test_timer.py",
                    "content": (
                        "from pathlib import Path\n\n"
                        "def test_timer_controls_and_completion():\n"
                        "    page = Path('index.html').read_text(encoding='utf-8')\n"
                        "    assert all(label in page for label in ('开始', '暂停', '继续', '专注完成'))\n"
                    ),
                    "requirement_ids": requirement_ids,
                })
            if path == "README.md":
                return self._call("read_file", {
                    "path": "README.md",
                    "start_line": 1,
                    "end_line": 120,
                })
            return self._call("run_command", {
                "command": "python3 -m pytest -q",
                "timeout": 10,
                "requirement_ids": requirement_ids,
            })
        if last.get("name") == "finish" and result.get("data", {}).get("checkpoint_kind") == "review":
            return self._call("read_file", {
                "path": "index.html",
                "start_line": 1,
                "end_line": 20,
                "requirement_ids": requirement_ids,
            })
        if last.get("name") == "finish" and result.get("data", {}).get("checkpoint_kind") == "verify":
            return self._call("run_command", {
                "command": "python3 -m pytest -q",
                "timeout": 10,
                "requirement_ids": requirement_ids,
            })
        if last.get("name") == "finish" and result.get("data", {}).get("checkpoint_kind") == "user_guide":
            instruction = str(result.get("data", {}).get("instruction", ""))
            if "创建或更新" in instruction:
                return self._call("create_file", {
                    "path": "README.md",
                    "content": (
                        "# 番茄钟\n\n## 项目简介与主要功能\n在浏览器中进行专注计时。\n\n"
                        "## 环境准备\n使用现代浏览器，无需安装依赖。\n\n"
                        "## 启动方法\n双击 index.html。\n\n"
                        "## 使用说明\n点击开始，可暂停和继续。\n\n"
                        "## 常见问题与注意事项\n页面异常时刷新浏览器。\n"
                    ),
                })
            return self._call("read_file", {"path": "README.md", "start_line": 1, "end_line": 120})
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
        loop = AgentLoop(events, SkillRouter(SKILLS_ROOT), model, max_steps=14)
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
    assert "traceability_initialized" in event_types
    assert "traceability_updated" in event_types
    assert record.traceability is not None
    assert record.traceability["verified"] == 2
    assert record.traceability["coverage_percent"] == 100
    assert record.user_guide_path == "README.md"
    assert (tmp_path / "README.md").is_file()
    assert "user_guide_ready" in event_types


def test_user_feedback_regenerates_interaction_model(tmp_path: Path) -> None:
    async def scenario() -> tuple[RunRecord, EventStore, InteractionFlowModel]:
        (tmp_path / "REQUIREMENTS.md").write_text("# 番茄钟需求\n", encoding="utf-8")
        events = EventStore(tmp_path / "events")
        events.create("run_revision")
        record = RunRecord("run_revision", "根据需求文档从零制作番茄钟前端页面", ".")
        model = InteractionFlowModel()
        loop = AgentLoop(events, SkillRouter(SKILLS_ROOT), model, max_steps=14)
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
    feedback_events = [event for event in channel.events if event["type"] == "user_requirement_received"]
    assert len(feedback_events) == 1
    assert feedback_events[0]["payload"]["message"] == "增加设置页"
    decisions = [
        event["payload"]["decision"]
        for event in channel.events
        if event["type"] == "interaction_confirmation_resolved"
    ]
    assert decisions == ["revise", "approve"]


def test_interaction_model_is_filtered_by_task_and_workspace(tmp_path: Path) -> None:
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "REQUIREMENTS.md").write_text("# 新网站需求\n", encoding="utf-8")
    build = RunRecord("run_build", "请根据需求文档从零实现一个知识卡片网页系统", "fresh")

    enabled, reason_code, _reason = _should_create_interaction_model(build, fresh)

    assert enabled is True
    assert reason_code == "greenfield_product_build"

    brief = RunRecord("run_brief", "继续完成这个项目", "fresh")
    enabled, reason_code, _reason = _should_create_interaction_model(brief, fresh)
    assert enabled is True
    assert reason_code == "requirements_backed_build"


def test_natural_language_web_request_starts_interaction_first(tmp_path: Path) -> None:
    """普通用户说“我想做一个网页”时，也必须先确认交互流程。"""
    (tmp_path / "REQUIREMENTS.md").write_text("", encoding="utf-8")
    record = RunRecord(
        "run_natural_language",
        "我想做一个适合学生使用的小组任务协作网页，帮助大家记录课程项目中的任务。",
        ".",
    )

    enabled, reason_code, _reason = _should_create_interaction_model(record, tmp_path)

    assert enabled is True
    assert reason_code == "greenfield_product_build"


def test_empty_source_directory_does_not_skip_interaction_first(tmp_path: Path) -> None:
    """只有空的 src 目录时仍视为新项目，而不是已有实现。"""
    (tmp_path / "src").mkdir()
    record = RunRecord("run_empty_src", "我想做一个简单网页", ".")

    enabled, reason_code, _reason = _should_create_interaction_model(record, tmp_path)

    assert enabled is True
    assert reason_code == "greenfield_product_build"

    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "package.json").write_text("{}", encoding="utf-8")
    maintenance = RunRecord("run_maintenance", "优化页面字体并修复按钮样式", "existing")
    enabled, reason_code, _reason = _should_create_interaction_model(maintenance, existing)
    assert enabled is False
    assert reason_code == "maintenance_task"

    explicit = RunRecord("run_explicit", "先为这次改版生成页面流转流程图", "existing")
    enabled, reason_code, _reason = _should_create_interaction_model(explicit, existing)
    assert enabled is True
    assert reason_code == "explicit_request"


def test_interaction_model_rejects_duplicate_flow_edges() -> None:
    arguments = {
        "title": "知识卡片",
        "summary": "用户创建并复习卡片。",
        "pages": [
            {"id": "list", "name": "卡片列表", "purpose": "查看和筛选卡片"},
            {"id": "editor", "name": "卡片编辑", "purpose": "创建一张卡片"},
        ],
        "flows": [
            {"from": "list", "action": "点击新建卡片", "to": "editor"},
            {"from": "list", "action": "点击新建卡片", "to": "editor"},
        ],
        "states": [{"from": "empty", "event": "创建卡片", "to": "active"}],
        "acceptance_criteria": [{
            "description": "可以创建卡片",
            "priority": "must",
            "verification": "automated_test",
        }],
    }

    try:
        _normalize_interaction_model(arguments)
    except ValueError as exc:
        assert "重复" in str(exc)
    else:
        raise AssertionError("重复页面流转应被拒绝并要求模型重新生成")
