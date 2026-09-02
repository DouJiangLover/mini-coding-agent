import asyncio

from backend.agent.hooks import (
    AfterToolContext,
    BeforeToolContext,
    BeforeToolDecision,
    QualityPreflightHook,
    RepeatedCallHook,
    UserGuideDeliveryHook,
    ToolHookManager,
    ToolRuntimeState,
    build_default_hook_manager,
)
from backend.state import RunRecord
from backend.tools.registry import ToolResult
from backend.traceability import TraceabilityLedger


class RecordingBeforeHook:
    def __init__(self, name: str, calls: list[str], *, block: bool = False) -> None:
        self.name = name
        self.calls = calls
        self.block = block

    async def before_tool_call(self, context: BeforeToolContext):
        self.calls.append(self.name)
        if not self.block:
            return None
        return BeforeToolDecision(
            action="block",
            hook_name=self.name,
            result=ToolResult(False, "被测试 Hook 阻止", error="blocked"),
        )


def test_before_hooks_are_ordered_and_short_circuit() -> None:
    calls: list[str] = []
    manager = ToolHookManager(before_hooks=[
        RecordingBeforeHook("first", calls),
        RecordingBeforeHook("guard", calls, block=True),
        RecordingBeforeHook("never", calls),
    ])

    decision = asyncio.run(manager.before_tool_call(BeforeToolContext(
        tool_name="create_file",
        arguments={"path": "app.py"},
        argument_error=None,
        state=ToolRuntimeState(),
    )))

    assert calls == ["first", "guard"]
    assert decision.action == "block"
    assert decision.hook_name == "guard"
    assert decision.result and not decision.result.ok


def test_preflight_and_repeat_guards_are_independent_hooks() -> None:
    preflight = QualityPreflightHook()
    state = ToolRuntimeState(requires_baseline=True)

    inspect_decision = asyncio.run(preflight.before_tool_call(BeforeToolContext(
        tool_name="apply_patch",
        arguments={"path": "app.py"},
        argument_error=None,
        state=state,
    )))
    assert inspect_decision and inspect_decision.checkpoint_item == "inspect"

    state.inspection_observed = True
    baseline_decision = asyncio.run(preflight.before_tool_call(BeforeToolContext(
        tool_name="apply_patch",
        arguments={"path": "app.py"},
        argument_error=None,
        state=state,
    )))
    assert baseline_decision and baseline_decision.checkpoint_item == "baseline"

    repeat = RepeatedCallHook(repeat_limit=3)
    repeated_context = BeforeToolContext(
        tool_name="read_file",
        arguments={"path": "app.py"},
        argument_error=None,
        state=state,
    )
    assert asyncio.run(repeat.before_tool_call(repeated_context)) is None
    assert asyncio.run(repeat.before_tool_call(repeated_context)) is None
    blocked = asyncio.run(repeat.before_tool_call(repeated_context))
    assert blocked and blocked.hook_name == "repeated_call_guard"


def test_after_hooks_update_run_state_and_traceability() -> None:
    manager = build_default_hook_manager()
    state = ToolRuntimeState(inspection_observed=True)
    record = RunRecord("run_hooks", "实现计数器", "demo")
    ledger = TraceabilityLedger([{
        "id": "AC-01",
        "description": "可以增加计数",
        "priority": "must",
        "verification": "automated_test",
    }])

    create_effects = asyncio.run(manager.after_tool_call(AfterToolContext(
        tool_name="create_file",
        arguments={"path": "app.py", "requirement_ids": ["AC-01"]},
        result=ToolResult(True, "已创建 app.py", {"path": "app.py"}),
        state=state,
        record=record,
        traceability=ledger,
    )))

    assert create_effects.changed_path == "app.py"
    assert create_effects.traceability_changed
    assert create_effects.applied_hooks == ["traceability_evidence", "run_observation"]
    assert record.changed_files == ["app.py"]
    assert record.traceability and record.traceability["requirements"][0]["status"] == "implemented"

    verify_effects = asyncio.run(manager.after_tool_call(AfterToolContext(
        tool_name="run_command",
        arguments={"command": "pytest -q", "requirement_ids": ["AC-01"]},
        result=ToolResult(True, "测试通过", {"command": "pytest -q", "exit_code": 0}),
        state=state,
        record=record,
        traceability=ledger,
    )))

    assert verify_effects.traceability_changed
    assert state.verification_after_change
    assert record.successful_commands == ["pytest -q"]
    assert record.traceability and record.traceability["requirements"][0]["status"] == "verified"


def test_user_guide_delivery_gate_requires_a_complete_reviewed_readme() -> None:
    gate = UserGuideDeliveryHook()
    manager = build_default_hook_manager()
    state = ToolRuntimeState(requires_user_guide=True, inspection_observed=True)
    record = RunRecord("run_guide", "从零实现网页产品", "demo")
    ledger = TraceabilityLedger([])

    missing = asyncio.run(gate.before_tool_call(BeforeToolContext(
        tool_name="finish",
        arguments={"summary": "完成"},
        argument_error=None,
        state=state,
    )))
    assert missing and missing.action == "checkpoint"
    assert missing.result and missing.result.data["checkpoint_kind"] == "user_guide"

    asyncio.run(manager.after_tool_call(AfterToolContext(
        tool_name="create_file",
        arguments={"path": "./README.md", "content": "placeholder"},
        result=ToolResult(True, "已创建 README.md", {"path": "./README.md"}),
        state=state,
        record=record,
        traceability=ledger,
    )))
    assert record.user_guide_path == "README.md"

    incomplete_effects = asyncio.run(manager.after_tool_call(AfterToolContext(
        tool_name="read_file",
        arguments={"path": "README.md"},
        result=ToolResult(True, "已读取", {"path": "README.md", "output": "# 项目简介\n一个小工具"}),
        state=state,
        record=record,
        traceability=ledger,
    )))
    assert not incomplete_effects.user_guide_ready_path
    incomplete = asyncio.run(gate.before_tool_call(BeforeToolContext(
        tool_name="finish",
        arguments={"summary": "完成"},
        argument_error=None,
        state=state,
    )))
    assert incomplete and "不完整" in (incomplete.result.summary if incomplete.result else "")

    complete_text = """# 项目简介
## 主要功能
完成任务。
## 环境准备
无需安装依赖。
## 启动方法
打开 index.html。
## 使用说明
点击开始按钮。
## 常见问题
请使用现代浏览器。
"""
    complete_effects = asyncio.run(manager.after_tool_call(AfterToolContext(
        tool_name="read_file",
        arguments={"path": "README.md"},
        result=ToolResult(True, "已读取", {"path": "README.md", "output": complete_text}),
        state=state,
        record=record,
        traceability=ledger,
    )))
    assert complete_effects.user_guide_ready_path == "README.md"
    assert asyncio.run(gate.before_tool_call(BeforeToolContext(
        tool_name="finish",
        arguments={"summary": "完成"},
        argument_error=None,
        state=state,
    ))) is None


def test_user_guide_becomes_stale_when_implementation_changes_after_it() -> None:
    manager = build_default_hook_manager()
    gate = UserGuideDeliveryHook()
    state = ToolRuntimeState(requires_user_guide=True, inspection_observed=True)
    record = RunRecord("run_stale_guide", "从零实现网页产品", "demo")
    ledger = TraceabilityLedger([])

    for path in ("README.md", "index.html"):
        asyncio.run(manager.after_tool_call(AfterToolContext(
            tool_name="create_file",
            arguments={"path": path, "content": "content"},
            result=ToolResult(True, f"已创建 {path}", {"path": path}),
            state=state,
            record=record,
            traceability=ledger,
        )))

    decision = asyncio.run(gate.before_tool_call(BeforeToolContext(
        tool_name="finish",
        arguments={"summary": "完成"},
        argument_error=None,
        state=state,
    )))
    assert decision and "晚于实现" in (decision.result.summary if decision.result else "")
