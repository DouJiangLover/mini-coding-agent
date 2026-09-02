from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal, Protocol

from backend.state import RunRecord
from backend.tools.registry import ToolResult
from backend.traceability import TraceabilityLedger


BeforeAction = Literal["allow", "block", "checkpoint"]


@dataclass
class ToolRuntimeState:
    """Mutable facts derived from real tool results during one run."""

    requires_baseline: bool = False
    inspection_observed: bool = False
    baseline_observed: bool = False
    verification_after_change: bool = False
    review_requested: bool = False
    review_evidence: bool = False
    requires_user_guide: bool = False
    user_guide_path: str = ""
    user_guide_stale: bool = False
    user_guide_reviewed: bool = False
    user_guide_topics: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class BeforeToolContext:
    tool_name: str
    arguments: dict
    argument_error: str | None
    state: ToolRuntimeState


@dataclass(frozen=True)
class BeforeToolDecision:
    action: BeforeAction
    hook_name: str
    result: ToolResult | None = None
    checkpoint_item: str = ""

    @classmethod
    def allow(cls) -> BeforeToolDecision:
        return cls(action="allow", hook_name="hook_pipeline")


@dataclass(frozen=True)
class AfterToolContext:
    tool_name: str
    arguments: dict
    result: ToolResult
    state: ToolRuntimeState
    record: RunRecord
    traceability: TraceabilityLedger


@dataclass
class AfterToolEffects:
    traceability_changed: bool = False
    changed_path: str = ""
    file_created: bool = False
    user_guide_ready_path: str = ""
    applied_hooks: list[str] = field(default_factory=list)


class BeforeToolHook(Protocol):
    name: str

    async def before_tool_call(self, context: BeforeToolContext) -> BeforeToolDecision | None:
        ...


class AfterToolHook(Protocol):
    name: str

    async def after_tool_call(self, context: AfterToolContext, effects: AfterToolEffects) -> None:
        ...


class ToolHookManager:
    """Runs trusted host hooks in a deterministic order.

    Hooks are Python objects registered by the host. Imported user Skills never
    become hooks and therefore cannot execute code inside the Agent process.
    """

    def __init__(
        self,
        *,
        before_hooks: list[BeforeToolHook] | None = None,
        after_hooks: list[AfterToolHook] | None = None,
    ) -> None:
        self.before_hooks = list(before_hooks or [])
        self.after_hooks = list(after_hooks or [])

    async def before_tool_call(self, context: BeforeToolContext) -> BeforeToolDecision:
        for hook in self.before_hooks:
            decision = await hook.before_tool_call(context)
            if decision and decision.action != "allow":
                return decision
        return BeforeToolDecision.allow()

    async def after_tool_call(self, context: AfterToolContext) -> AfterToolEffects:
        effects = AfterToolEffects()
        for hook in self.after_hooks:
            await hook.after_tool_call(context, effects)
            effects.applied_hooks.append(hook.name)
        return effects


class ArgumentValidationHook:
    name = "argument_validation"

    async def before_tool_call(self, context: BeforeToolContext) -> BeforeToolDecision | None:
        if not context.argument_error:
            return None
        return BeforeToolDecision(
            action="block",
            hook_name=self.name,
            result=ToolResult(
                False,
                f"{context.tool_name} 参数解析失败",
                error=context.argument_error,
            ),
        )


class RepeatedCallHook:
    name = "repeated_call_guard"

    def __init__(self, repeat_limit: int = 3) -> None:
        self.repeat_limit = max(2, repeat_limit)
        self._recent_fingerprints: list[str] = []

    async def before_tool_call(self, context: BeforeToolContext) -> BeforeToolDecision | None:
        fingerprint = json.dumps(
            {"name": context.tool_name, "arguments": context.arguments},
            ensure_ascii=False,
            sort_keys=True,
        )
        repeated = (
            len(self._recent_fingerprints) >= self.repeat_limit - 1
            and self._recent_fingerprints[-(self.repeat_limit - 1):]
            == [fingerprint] * (self.repeat_limit - 1)
        )
        self._recent_fingerprints.append(fingerprint)
        self._recent_fingerprints = self._recent_fingerprints[-(self.repeat_limit + 1):]
        if not repeated:
            return None
        return BeforeToolDecision(
            action="block",
            hook_name=self.name,
            result=ToolResult(
                False,
                "检测到重复工具调用",
                error=f"同一动作连续出现{self.repeat_limit}次，已阻止执行；请换一种方法",
            ),
        )


class QualityPreflightHook:
    name = "quality_preflight"

    async def before_tool_call(self, context: BeforeToolContext) -> BeforeToolDecision | None:
        if context.tool_name not in {"create_file", "apply_patch"}:
            return None
        if not context.state.inspection_observed:
            return BeforeToolDecision(
                action="checkpoint",
                hook_name=self.name,
                checkpoint_item="inspect",
                result=_quality_checkpoint(
                    "inspect",
                    "修改前需要先理解项目",
                    "请先使用 list_files、read_file 或 search_text 收集项目证据，再实施修改。",
                ),
            )
        if context.state.requires_baseline and not context.state.baseline_observed:
            return BeforeToolDecision(
                action="checkpoint",
                hook_name=self.name,
                checkpoint_item="baseline",
                result=_quality_checkpoint(
                    "baseline",
                    "修复前需要建立问题基线",
                    "请先运行现有测试或检查命令，记录修改前的失败结果。",
                ),
            )
        return None


class UserGuideDeliveryHook:
    """Prevents a newly built end-user product from finishing without usable docs."""

    name = "user_guide_delivery"

    async def before_tool_call(self, context: BeforeToolContext) -> BeforeToolDecision | None:
        state = context.state
        if context.tool_name != "finish" or not state.requires_user_guide:
            return None
        if not state.user_guide_path:
            return _guide_checkpoint(
                self.name,
                "缺少终端用户使用说明",
                "请在工作区根目录创建或更新 README.md。面向非开发人员写清项目功能、环境准备、启动方法、使用步骤和常见问题；不得写入 API Key。",
            )
        if state.user_guide_stale:
            return _guide_checkpoint(
                self.name,
                "README 晚于实现发生了变化",
                f"请根据最终实现更新 {state.user_guide_path}，然后重新读取并检查内容。",
            )
        if not state.user_guide_reviewed:
            return _guide_checkpoint(
                self.name,
                "README 尚未经过交付检查",
                f"请完整读取 {state.user_guide_path}，确认非开发人员能够按文档启动和使用项目。",
            )
        missing = [
            label for key, label in USER_GUIDE_TOPIC_LABELS.items()
            if key not in state.user_guide_topics
        ]
        if missing:
            return _guide_checkpoint(
                self.name,
                "README 使用信息不完整",
                f"请补充以下内容后重新读取检查：{'、'.join(missing)}。",
            )
        return None


class TraceabilityEvidenceHook:
    name = "traceability_evidence"

    async def after_tool_call(self, context: AfterToolContext, effects: AfterToolEffects) -> None:
        if context.result.data.get("checkpoint_required"):
            return
        changed = context.traceability.observe(
            context.tool_name,
            context.arguments,
            context.result,
        )
        if changed:
            context.record.traceability = context.traceability.public_dict()
            effects.traceability_changed = True


class RunObservationHook:
    name = "run_observation"

    async def after_tool_call(self, context: AfterToolContext, effects: AfterToolEffects) -> None:
        name = context.tool_name
        result = context.result
        record = context.record
        state = context.state

        if name in {"create_file", "apply_patch"} and result.ok:
            changed_path = str(result.data.get("path", ""))
            if changed_path and changed_path not in record.changed_files:
                record.changed_files.append(changed_path)
            effects.changed_path = changed_path
            effects.file_created = name == "create_file"
            state.verification_after_change = False
            state.review_requested = False
            state.review_evidence = False
            if _is_root_user_guide(changed_path):
                state.user_guide_path = _normalized_user_guide_path(changed_path)
                state.user_guide_stale = False
                state.user_guide_reviewed = False
                state.user_guide_topics.clear()
                record.user_guide_path = state.user_guide_path
            elif state.user_guide_path:
                state.user_guide_stale = True
                state.user_guide_reviewed = False

        if name == "run_command" and "exit_code" in result.data and not record.changed_files:
            state.baseline_observed = True
        if name == "run_command" and result.ok:
            command = str(result.data.get("command", ""))
            if command:
                record.successful_commands.append(command)
            if record.changed_files:
                state.verification_after_change = True
        if name in {"list_files", "read_file", "search_text"} and result.ok:
            state.inspection_observed = True
        if name == "read_file" and result.ok:
            read_path = str(result.data.get("path", ""))
            if read_path and read_path not in record.read_files:
                record.read_files.append(read_path)
            if _normalized_user_guide_path(read_path) == state.user_guide_path and not state.user_guide_stale:
                state.user_guide_topics = _user_guide_topics(str(result.data.get("output", "")))
                state.user_guide_reviewed = True
                if set(USER_GUIDE_TOPIC_LABELS).issubset(state.user_guide_topics):
                    effects.user_guide_ready_path = read_path
        if state.review_requested and result.ok and _is_review_action(name, context.arguments, record.changed_files):
            state.review_evidence = True


def build_default_hook_manager() -> ToolHookManager:
    return ToolHookManager(
        before_hooks=[
            ArgumentValidationHook(),
            RepeatedCallHook(),
            QualityPreflightHook(),
            UserGuideDeliveryHook(),
        ],
        after_hooks=[
            TraceabilityEvidenceHook(),
            RunObservationHook(),
        ],
    )


def _quality_checkpoint(kind: str, summary: str, instruction: str) -> ToolResult:
    return ToolResult(
        False,
        summary,
        data={
            "checkpoint_required": True,
            "checkpoint_kind": kind,
            "instruction": instruction,
        },
        error=instruction,
    )


USER_GUIDE_TOPIC_LABELS = {
    "overview": "项目功能介绍",
    "setup": "环境准备或安装说明",
    "start": "可直接照做的启动方法",
    "usage": "具体使用步骤",
    "help": "常见问题或注意事项",
}

USER_GUIDE_TOPIC_MARKERS = {
    "overview": ("项目简介", "项目介绍", "功能介绍", "主要功能", "overview", "features"),
    "setup": ("环境要求", "环境准备", "准备工作", "安装", "requirements", "prerequisite", "installation"),
    "start": ("快速开始", "启动方法", "如何启动", "运行项目", "getting started", "start", "run"),
    "usage": ("使用说明", "使用方法", "如何使用", "操作步骤", "usage", "how to use"),
    "help": ("常见问题", "故障排查", "注意事项", "faq", "troubleshooting"),
}


def _guide_checkpoint(hook_name: str, summary: str, instruction: str) -> BeforeToolDecision:
    return BeforeToolDecision(
        action="checkpoint",
        hook_name=hook_name,
        checkpoint_item="guide",
        result=_quality_checkpoint("user_guide", summary, instruction),
    )


def _is_root_user_guide(path: str) -> bool:
    return _normalized_user_guide_path(path).casefold() in {"readme.md", "readme.txt"}


def _normalized_user_guide_path(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./")


def _user_guide_topics(content: str) -> set[str]:
    normalized = content.casefold()
    return {
        topic for topic, markers in USER_GUIDE_TOPIC_MARKERS.items()
        if any(marker.casefold() in normalized for marker in markers)
    }


def _is_review_action(name: str, arguments: dict, changed_files: list[str]) -> bool:
    if name == "read_file":
        return str(arguments.get("path", "")) in changed_files
    if name == "run_command":
        return str(arguments.get("command", "")).strip().startswith("git diff")
    return False
