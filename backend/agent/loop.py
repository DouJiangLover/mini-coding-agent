from __future__ import annotations

import asyncio
import json
import math
import os
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from backend.agent.hooks import (
    AfterToolContext,
    BeforeToolContext,
    ToolRuntimeState,
    build_default_hook_manager,
)
from backend.events.store import EventStore
from backend.llm.client import ModelClient
from backend.settings import AgentSettings, AgentSettingsStore
from backend.sessions.store import SessionStore
from backend.skills.router import SkillMatch, SkillRouter
from backend.state import RunRecord
from backend.tools.registry import ToolRegistry, ToolResult
from backend.traceability import TraceabilityLedger


TOOL_TITLES = {
    "load_skill": "加载 Skill",
    "list_files": "查看项目结构",
    "read_file": "读取文件",
    "search_text": "搜索代码",
    "create_file": "创建文件",
    "apply_patch": "应用局部补丁",
    "run_command": "运行命令",
    "finish": "提交任务结果",
}

PHASE_COPY = {
    "interaction_modeling": ("正在建模产品交互", "从终端用户视角生成页面流转和状态变化。"),
    "interaction_confirmation": ("等待确认交互流程", "用户确认产品流程后才会进入代码实现。"),
    "inspecting": ("正在理解项目", "先确认目录、需求和相关代码，避免在信息不足时修改。"),
    "reproducing": ("正在建立问题基线", "运行现有测试或检查命令，获取修改前的真实结果。"),
    "diagnosing": ("正在定位根因", "结合基线输出继续读取相关实现和测试。"),
    "implementing": ("正在实施修改", "基于已收集证据进行聚焦修改。"),
    "documenting": ("正在编写使用说明", "根据最终项目事实整理非开发人员可直接照做的 README。"),
    "verifying": ("正在验证修改", "运行验证命令检查任务结果。"),
    "reviewing": ("正在完成前自检", "重新审查改动文件，检查需求覆盖和潜在副作用。"),
}

INTERACTION_EXPLICIT_SIGNALS = ("流程图", "交互流程", "页面流转", "用户旅程")
INTERACTION_BUILD_SIGNALS = (
    "从零", "需求文档", "新建", "创建一个", "帮我创建", "制作一个", "帮我做", "我想做", "想做一个",
    "做一个", "搭建", "开发一个", "实现一个", "网页", "网页系统", "web 系统", "web 应用", "网站",
    "应用", "系统", "小程序", "小游戏", "工具", "平台",
)
INTERACTION_MAINTENANCE_SIGNALS = (
    "修复", "报错", "错误", "bug", "测试失败", "补测试", "代码审查", "解释", "为什么",
    "调整字体", "修改样式", "优化页面",
)
INTERACTION_REQUIREMENTS_TASK_SIGNALS = (
    "实现", "完成", "继续", "开始", "按照", "根据", "开发", "制作", "构建", "做这个项目",
)

INTERACTION_MODEL_TOOL = [{
    "type": "function",
    "function": {
        "name": "submit_interaction_model",
        "description": "提交终端用户视角的页面流转、状态机和验收标准，供用户确认。",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "目标产品名称"},
                "summary": {"type": "string", "description": "一句话说明核心用户目标"},
                "pages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string", "description": "简短、唯一的页面或弹层名称"},
                            "purpose": {"type": "string", "description": "一句话说明用户在这里完成什么，不罗列实现细节"},
                        },
                        "required": ["id", "name", "purpose"],
                        "additionalProperties": False,
                    },
                },
                "flows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "from": {"type": "string", "description": "起点页面 id"},
                            "action": {"type": "string", "description": "明确的动词加对象，例如“点击保存并返回列表”，避免“处理”“下一步”等模糊词"},
                            "to": {"type": "string", "description": "目标页面 id"},
                        },
                        "required": ["from", "action", "to"],
                        "additionalProperties": False,
                    },
                },
                "states": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "from": {"type": "string"},
                            "event": {"type": "string"},
                            "to": {"type": "string"},
                        },
                        "required": ["from", "event", "to"],
                        "additionalProperties": False,
                    },
                },
                "acceptance_criteria": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "priority": {"type": "string", "enum": ["must", "should"]},
                            "verification": {
                                "type": "string",
                                "enum": ["automated_test", "build_check", "human_review"],
                            },
                        },
                        "required": ["description", "priority", "verification"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["title", "summary", "pages", "flows", "states", "acceptance_criteria"],
            "additionalProperties": False,
        },
    },
}]

class AgentLoop:
    def __init__(
        self,
        events: EventStore,
        skill_router: SkillRouter,
        model: ModelClient,
        max_steps: int | None = None,
        settings_store: AgentSettingsStore | None = None,
        session_store: SessionStore | None = None,
    ) -> None:
        self.events = events
        self.skill_router = skill_router
        self.model = model
        self.max_steps_override = max_steps
        self.environment_max_steps = self._environment_step_budget()
        self.settings_store = settings_store
        self.session_store = session_store

    @staticmethod
    def _environment_step_budget() -> int | None:
        raw = (os.getenv("INTENTFLOW_MAX_STEPS") or os.getenv("TRACECODER_MAX_STEPS") or "").strip()
        if not raw:
            return None
        try:
            parsed = int(raw)
        except ValueError:
            return None
        return parsed if 5 <= parsed <= 100 else None

    def _resolve_max_steps(self, settings: AgentSettings) -> tuple[int, str]:
        if self.max_steps_override is not None:
            return self.max_steps_override, "constructor_override"
        # Once the user saves the settings page, that explicit choice must win.
        # The environment variable only seeds installations without saved settings.
        if settings.updated_at:
            return settings.max_steps, "saved_settings"
        if self.environment_max_steps is not None:
            return self.environment_max_steps, "environment_default"
        return settings.max_steps, "built_in_default"

    async def run(self, record: RunRecord, workspace: Path) -> None:
        try:
            await self._run(record, workspace)
        except asyncio.CancelledError:
            await self._finish(record, "cancelled", "任务已被用户停止。")
        except Exception as exc:
            await self.events.emit(
                record.run_id, "error", record.phase, "failed", "Agent 运行异常",
                f"{type(exc).__name__}: {exc}", {"error_type": type(exc).__name__},
            )
            await self._finish(record, "failed", f"任务因不可恢复错误停止：{exc}")

    async def _run(self, record: RunRecord, workspace: Path) -> None:
        settings = self.settings_store.get() if self.settings_store else AgentSettings()
        max_steps, max_steps_source = self._resolve_max_steps(settings)
        failure_limit = settings.failure_limit
        record.status = "running"
        record.phase = "selecting_skill"
        await self.events.emit(
            record.run_id, "run_started", record.phase, "running", "任务已启动",
            f"工作区：{record.workspace} · 实际步数预算：{max_steps}", {
                "task": record.task,
                "root_task": record.root_task,
                "parent_run_id": record.parent_run_id,
                "session_id": record.session_id,
                "session_entry_id": record.session_entry_id,
                "parent_entry_id": record.parent_entry_id,
                "continuation_context": record.continuation_context,
                "workspace": record.workspace,
                "requested_skill": record.requested_skill,
                "mode": self.model.mode_name,
                "agent_settings": settings.public_dict(),
                "effective_max_steps": max_steps,
                "max_steps_source": max_steps_source,
            },
        )

        if record.parent_run_id:
            await self.events.emit(
                record.run_id,
                "continuation_loaded",
                record.phase,
                "success",
                "已恢复工作区对话",
                "已继承上一轮目标、进度、错误和文件改动；将从当前工作区状态继续。",
                {"parent_run_id": record.parent_run_id, "root_task": record.root_task},
            )

        # Interaction-First is a host workflow decision, not a Skill capability.
        # It runs from task/workspace evidence before Skill routing, so an
        # imprecise Skill match cannot accidentally enable or suppress it.
        interaction_model = (
            record.continuation_context.get("confirmed_interaction_model")
            if record.continuation_context else None
        )
        if not settings.interaction_first:
            interaction_enabled = False
            interaction_reason = "设置页已关闭 Interaction-First。"
            interaction_reason_code = "disabled_in_settings"
        elif interaction_model:
            interaction_enabled = True
            interaction_reason = "续做任务已存在用户确认过的交互流程，直接沿用，不重复询问。"
            interaction_reason_code = "restored_confirmation"
        else:
            interaction_enabled, interaction_reason_code, interaction_reason = _should_create_interaction_model(
                record,
                workspace,
            )
        await self.events.emit(
            record.run_id,
            "interaction_model_decision",
            record.phase,
            "success",
            "需要交互流程确认" if interaction_enabled else "已跳过交互流程确认",
            interaction_reason,
            {
                "enabled": interaction_enabled,
                "reason_code": interaction_reason_code,
                "decision_source": "task_and_workspace_analyzer",
            },
        )

        if interaction_enabled and interaction_model:
            await self.events.emit(
                record.run_id,
                "interaction_context_restored",
                "interaction_modeling",
                "success",
                "已恢复确认过的交互流程",
                "续跑任务将沿用上一轮已经确认的页面、流转和验收标准。",
                {"model_id": interaction_model.get("model_id", "")},
            )
        elif interaction_enabled:
            interaction_model = await self._interaction_first(record, workspace)
            if interaction_model is None:
                await self._finish(record, "cancelled", "任务在等待产品交互确认时被用户停止。")
                return

        record.phase = "selecting_skill"
        matches = await self._select_skills(record, interaction_model=interaction_model)
        if matches is None:
            await self._finish(record, "cancelled", "任务在等待 Skill 选择时被用户停止。")
            return

        record.phase = "planning"
        await self.events.emit(
            record.run_id, "phase_changed", record.phase, "running", "正在制定计划",
            "根据 Skill 策略建立可追踪的执行步骤。",
        )
        restored_traceability = (
            record.continuation_context.get("traceability")
            if record.continuation_context and isinstance(record.continuation_context.get("traceability"), dict)
            else None
        )
        traceability = TraceabilityLedger(
            interaction_model.get("acceptance_criteria", []) if interaction_model else [],
            restored=restored_traceability,
        )
        record.traceability = traceability.public_dict() if traceability.active else None
        requires_user_guide = _requires_user_guide(record, workspace, matches, interaction_model)
        plan_items = _with_user_guide_plan(matches[0].skill.plan) if requires_user_guide else matches[0].skill.plan
        plan = PlanTracker(plan_items, requirement_ids=traceability.requirement_ids)
        await self._emit_plan(record, plan)
        if traceability.active:
            await self.events.emit(
                record.run_id,
                "traceability_initialized",
                record.phase,
                "info",
                "需求追踪已建立",
                f"已将 {len(traceability.requirement_ids)} 项确认需求连接到实现计划与验证证据。",
                record.traceability,
            )

        registry = ToolRegistry(
            workspace,
            _combined_skill_tools(matches),
            agent_mode=settings.mode,
            max_command_timeout=settings.command_timeout,
            requirement_ids=traceability.requirement_ids,
            skill_library={
                match.skill.name: {
                    "display_name": match.skill.display_name,
                    "description": match.skill.description,
                    "prompt": match.skill.prompt,
                }
                for match in matches
            },
        )
        messages = self._initial_messages(record, matches, workspace, interaction_model, settings)
        record.phase = "inspecting"
        await self.events.emit(
            record.run_id, "phase_changed", record.phase, "running", "进入执行阶段",
            "先理解工作区，再建立基线、定位、修改、验证并自检。",
        )

        consecutive_failures = 0
        no_tool_turns = 0
        runtime_state = ToolRuntimeState(
            requires_baseline=any(match.skill.name in {"bug_fix", "test_writer"} for match in matches),
            requires_user_guide=requires_user_guide,
        )
        hook_manager = build_default_hook_manager()
        step_delay = float(
            os.getenv("INTENTFLOW_STEP_DELAY")
            or os.getenv("TRACECODER_STEP_DELAY")
            or ("0.2" if self.model.mode_name == "demo" else "0")
        )

        for _step in range(1, max_steps + 1):
            if record.cancel_requested:
                await self._finish(record, "cancelled", "任务已被用户停止。")
                return

            steering = record.take_pending_steering()
            if steering:
                messages.append({
                    "role": "user",
                    "content": (
                        "以下是用户在本次运行中刚刚发送的方向修正。它是当前任务的最新要求，"
                        "请从下一项行动开始据此调整方案；它不会授权危险操作，也不能绕过宿主安全与质量关卡：\n"
                        + "\n".join(f"- {item['message']}" for item in steering)
                    ),
                })
                for item in steering:
                    if self.session_store and record.session_id and record.session_entry_id:
                        self.session_store.mark_steering_applied(
                            record.session_id,
                            record.session_entry_id,
                            str(item["steering_id"]),
                            str(item["applied_at"]),
                        )
                    await self.events.emit(
                        record.run_id,
                        "steering_applied",
                        record.phase,
                        "success",
                        "方向修正已进入上下文",
                        str(item["message"]),
                        {
                            "steering_id": item["steering_id"],
                            "message": item["message"],
                            "status": "applied",
                            "applied_at": item["applied_at"],
                        },
                    )

            messages, compacted_summary = _compact_context(
                messages,
                max_chars=settings.context_budget,
                goal=record.root_task,
                current_request=record.task,
                plan_items=plan.items,
                changed_files=record.changed_files,
                successful_commands=record.successful_commands,
                traceability=record.traceability,
                steering_messages=record.steering_messages,
            )
            if compacted_summary:
                await self.events.emit(
                    record.run_id,
                    "context_compacted",
                    record.phase,
                    "info",
                    "上下文已结构化压缩",
                    "保留目标、进度、文件记录、命令和最近错误；完整历史仍保存在 Session 中。",
                    compacted_summary,
                )
                if self.session_store and record.session_id and record.session_entry_id:
                    self.session_store.append_compaction(
                        record.session_id,
                        record.session_entry_id,
                        compacted_summary,
                    )
            turn = await self.model.complete(messages, registry.schemas())
            messages.append(turn.assistant_message)
            if not turn.tool_calls:
                no_tool_turns += 1
                messages.append({
                    "role": "user",
                    "content": "请继续使用可用工具推进任务；完成时必须调用 finish，不要只输出自然语言。",
                })
                if no_tool_turns >= failure_limit:
                    await self._finish(record, "failed", f"模型连续{_failure_count_text(failure_limit)}未返回工具调用，任务已停止。")
                    return
                continue
            no_tool_turns = 0

            for call in turn.tool_calls:
                if record.cancel_requested:
                    await self._finish(record, "cancelled", "任务已被用户停止。")
                    return
                if step_delay:
                    await asyncio.sleep(step_delay)

                before_decision = await hook_manager.before_tool_call(
                    BeforeToolContext(
                        tool_name=call.name,
                        arguments=call.arguments,
                        argument_error=call.argument_error,
                        state=runtime_state,
                    ),
                )
                if before_decision.action == "checkpoint":
                    checkpoint_item = before_decision.checkpoint_item
                    plan.require(checkpoint_item)
                    next_phase = {
                        "inspect": "inspecting",
                        "baseline": "reproducing",
                        "guide": "documenting",
                    }.get(checkpoint_item, record.phase)
                else:
                    plan.on_tool_started(
                        call.name,
                        bool(record.changed_files),
                        baseline_observed=runtime_state.baseline_observed,
                        review_requested=runtime_state.review_requested,
                    )
                    next_phase = record.phase if call.name in {"load_skill", "finish"} else _phase_for_tool(
                        call.name,
                        has_changes=bool(record.changed_files),
                        baseline_observed=runtime_state.baseline_observed,
                        review_requested=runtime_state.review_requested,
                    )
                    if _is_user_guide_call(call.name, call.arguments):
                        plan.require("guide")
                        next_phase = "documenting"
                await self._set_phase(record, next_phase)
                await self._emit_plan(record, plan)

                title = TOOL_TITLES.get(call.name, call.name)
                await self.events.emit(
                    record.run_id, "tool_started", record.phase, "running", title,
                    _tool_summary(call.name, call.arguments),
                    {"tool": call.name, "arguments": _safe_arguments(call.arguments)},
                )

                if before_decision.result is not None:
                    result = before_decision.result
                else:
                    result = await registry.execute(call.name, call.arguments)

                if result.approval_required:
                    result = await self._request_approval(record, registry, call.name, call.arguments, result)
                    if result is None:
                        await self._finish(record, "cancelled", "任务在等待授权时被用户停止。", plan)
                        return

                if call.name == "finish" and result.ok:
                    traceability_gaps = traceability.blocking_gaps() if traceability.active else []
                    if traceability_gaps:
                        result = _quality_checkpoint(
                            "requirements",
                            "已确认需求尚未形成完整证据",
                            traceability.gap_instruction(),
                            traceability=traceability.public_dict(),
                            gaps=traceability_gaps,
                        )
                        plan.require("verify" if record.changed_files else "edit")
                        await self._set_phase(record, "verifying" if record.changed_files else "implementing")
                    elif settings.require_verification and record.changed_files and not runtime_state.verification_after_change:
                        result = _quality_checkpoint(
                            "verify",
                            "修改后还没有成功的验证结果",
                            "请运行与任务对应的测试或检查命令；验证通过后再调用 finish。",
                        )
                        plan.require("verify")
                        await self._set_phase(record, "verifying")
                    elif settings.require_review and record.changed_files and not runtime_state.review_requested:
                        runtime_state.review_requested = True
                        result = _quality_checkpoint(
                            "review",
                            "进入完成前自检",
                            f"请重新读取至少一个改动文件，优先检查 {record.changed_files[-1]}，确认没有无关修改或遗漏。",
                            review_path=record.changed_files[-1],
                        )
                        plan.require("review")
                        await self._set_phase(record, "reviewing")
                    elif settings.require_review and record.changed_files and not runtime_state.review_evidence:
                        result = _quality_checkpoint(
                            "review",
                            "自检证据不足",
                            f"请读取改动文件 {record.changed_files[-1]} 后再提交完成结果。",
                            review_path=record.changed_files[-1],
                        )
                        plan.require("review")
                        await self._set_phase(record, "reviewing")

                if result.data.get("checkpoint_required"):
                    await self.events.emit(
                        record.run_id, "quality_checkpoint", record.phase, "info", result.summary,
                        result.error or "需要完成质量检查点后继续。", result.data,
                    )

                after_effects = await hook_manager.after_tool_call(
                    AfterToolContext(
                        tool_name=call.name,
                        arguments=call.arguments,
                        result=result,
                        state=runtime_state,
                        record=record,
                        traceability=traceability,
                    ),
                )
                payload = {
                    "tool": call.name,
                    "arguments": _safe_arguments(call.arguments),
                    "hook_pipeline": {
                        "before": {
                            "decision": before_decision.action,
                            "hook": before_decision.hook_name,
                        },
                        "after": after_effects.applied_hooks or [],
                    },
                    **result.data,
                }
                if after_effects.traceability_changed:
                    payload["traceability"] = record.traceability
                if result.error:
                    payload["error"] = result.error
                await self.events.emit(
                    record.run_id, "tool_finished", record.phase,
                    "success" if result.ok else "info" if result.data.get("checkpoint_required") else "failed",
                    title, result.summary, payload,
                )
                if after_effects.traceability_changed:
                    await self.events.emit(
                        record.run_id,
                        "traceability_updated",
                        record.phase,
                        "success" if not traceability.blocking_gaps() else "info",
                        "需求证据已更新",
                        f"当前覆盖 {record.traceability['verified']}/{record.traceability['total']} 项已确认需求。",
                        record.traceability,
                    )

                if after_effects.changed_path:
                    await self.events.emit(
                        record.run_id, "file_changed", record.phase, "success",
                        f"文件{'已创建' if after_effects.file_created else '已修改'} · {after_effects.changed_path}",
                        "新文件内容可在 Diff 标签中审查。" if after_effects.file_created else "局部补丁已应用，可在 Diff 标签中审查。",
                        result.data,
                    )

                if after_effects.user_guide_ready_path:
                    plan.complete("guide")
                    await self.events.emit(
                        record.run_id, "user_guide_ready", record.phase, "success",
                        "终端用户使用说明已就绪",
                        f"已检查 {after_effects.user_guide_ready_path}：包含功能、准备、启动、使用和常见问题。",
                        {"path": after_effects.user_guide_ready_path},
                    )

                plan.on_tool_finished(
                    call.name,
                    result.ok,
                    bool(record.changed_files),
                    baseline_observed=runtime_state.baseline_observed,
                    review_requested=runtime_state.review_requested,
                    review_evidence=runtime_state.review_evidence,
                )
                await self._emit_plan(record, plan)

                messages.append({
                    "role": "tool",
                    "tool_call_id": call.call_id,
                    "name": call.name,
                    "content": json.dumps(result.to_dict(), ensure_ascii=False),
                })

                if call.name == "finish" and result.ok:
                    if record.has_pending_steering():
                        await self.events.emit(
                            record.run_id,
                            "steering_deferred_completion",
                            record.phase,
                            "info",
                            "已暂停完成并等待应用新方向",
                            "Agent 会在下一次模型决策前读取刚收到的 Steering 消息。",
                        )
                        break
                    summary = str(result.data.get("summary", result.summary))
                    verification = str(result.data.get("verification", ""))
                    if verification:
                        summary = f"{summary} 验证：{verification}。"
                    await self._finish(record, "completed", summary, plan)
                    return

                if result.ok:
                    consecutive_failures = 0
                elif (
                    result.data.get("user_denied")
                    or result.data.get("checkpoint_required")
                    or (call.name == "run_command" and "exit_code" in result.data)
                ):
                    consecutive_failures = 0
                    if call.name == "run_command" and "exit_code" in result.data and not result.ok:
                        await self._set_phase(record, "diagnosing")
                else:
                    consecutive_failures += 1
                    record.phase = "recovering"
                    await self.events.emit(
                        record.run_id, "error", record.phase, "failed", "工具反馈需要处理",
                        result.error or result.summary, {"tool": call.name, "recoverable": consecutive_failures < 3},
                    )
                    if consecutive_failures >= failure_limit:
                        await self._finish(record, "failed", f"连续{_failure_count_text(failure_limit)}工具执行失败，Agent 已停止以避免无效循环。", plan)
                        return

        await self._finish(record, "failed", f"达到最大执行步数 {max_steps}，任务尚未完成。", plan)

    async def _select_skills(
        self,
        record: RunRecord,
        *,
        interaction_model: dict[str, Any] | None = None,
    ) -> list[SkillMatch] | None:
        routing_task = record.task
        if record.parent_run_id:
            routing_task = f"原始目标：{record.root_task}\n当前补充：{record.task}"
        if interaction_model:
            routing_task += (
                "\n工作流证据：这是需要根据已确认终端用户流程从零构建的网页产品。"
                f"产品：{interaction_model.get('title', '')}；目标：{interaction_model.get('summary', '')}"
            )
        if record.requested_skill != "auto":
            match = self.skill_router.match_enabled(record.requested_skill, routing_task)
            await self._emit_skills_selected(
                record,
                [match],
                confidence=1.0,
                strategy="manual",
                reason="用户在任务开始前手动指定了该 Skill。",
                candidates=[match],
            )
            return [match]

        ranked = self.skill_router.rank(routing_task)
        candidates = ranked[:5] if ranked[0].score > 0 else ranked[:8]
        candidate_payloads = [self._skill_candidate_payload(item) for item in candidates]
        await self.events.emit(
            record.run_id,
            "skill_candidates",
            record.phase,
            "success",
            "已生成 Skill 候选",
            "先按触发词筛选候选，再结合任务语义组合互补能力。",
            {"candidates": candidate_payloads},
        )

        selected, confidence, reason, strategy = await self._semantic_skill_choice(routing_task, candidates)
        await self._emit_skills_selected(
            record,
            selected,
            confidence=confidence,
            strategy=strategy,
            reason=reason,
            candidates=candidates,
        )
        return selected

    async def _semantic_skill_choice(
        self,
        task: str,
        candidates: list[SkillMatch],
    ) -> tuple[list[SkillMatch], float, str, str]:
        positively_matched = [item for item in candidates if item.score > 0]
        fallback = positively_matched[:3] or [candidates[0]]
        fallback_confidence = self._keyword_confidence(candidates)
        fallback_reason = "关键词路由：" + "；".join(item.reason for item in fallback) + "。"
        if self.model.mode_name != "model":
            return fallback, fallback_confidence, fallback_reason, "keyword"

        names = [item.skill.name for item in candidates]
        maximum = min(3, len(names))
        tool = [{
            "type": "function",
            "function": {
                "name": "select_skills",
                "description": "从候选 Skill 中选择一到三项互补能力，并给出整体置信度和简短理由。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_names": {
                            "type": "array",
                            "items": {"type": "string", "enum": names},
                            "minItems": 1,
                            "maxItems": maximum,
                            "uniqueItems": True,
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "reason": {"type": "string"},
                    },
                    "required": ["skill_names", "confidence", "reason"],
                    "additionalProperties": False,
                },
            },
        }]
        candidate_context = [{
            "name": item.skill.name,
            "display_name": item.skill.display_name,
            "description": item.skill.description,
            "keywords": item.skill.keywords,
            "keyword_score": item.score,
            "matched_keywords": item.matched_keywords,
        } for item in candidates]
        messages = [
            {
                "role": "system",
                "content": (
                    "你是编程智能体的多 Skill 路由器。只能从候选项中选择一到三项真正相关且互补的 Skill。"
                    "简单任务只选一项；任务同时包含例如修复、补测试、写文档或前端构建等不同目标时才组合多项。"
                    "避免选择重复或无关能力。结合完整任务语义、Skill 描述和关键词得分判断；"
                    "不要执行任务，只调用 select_skills。交互流程图由宿主工作流独立决定，不属于任何 Skill。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"task": task, "candidates": candidate_context}, ensure_ascii=False),
            },
        ]
        try:
            turn = await self.model.complete(messages, tool)
            call = next((item for item in turn.tool_calls if item.name == "select_skills"), None)
            if not call or call.argument_error:
                return fallback, fallback_confidence, fallback_reason, "keyword_fallback"
            raw_names = call.arguments.get("skill_names", [])
            if not isinstance(raw_names, list):
                return fallback, fallback_confidence, fallback_reason, "keyword_fallback"
            selected_names = list(dict.fromkeys(str(name) for name in raw_names))[:maximum]
            selected = [
                item for name in selected_names
                for item in candidates
                if item.skill.name == name
            ]
            if not selected or len(selected) != len(selected_names):
                return fallback, fallback_confidence, fallback_reason, "keyword_fallback"
            confidence = float(call.arguments.get("confidence", 0))
            if not math.isfinite(confidence):
                raise ValueError("Skill 路由置信度必须是有限数字")
            confidence = max(0.0, min(1.0, confidence))
            reason = str(call.arguments.get("reason", "")).strip() or "；".join(item.reason for item in selected)
            return selected, confidence, reason, "semantic"
        except Exception:
            return fallback, fallback_confidence, fallback_reason, "keyword_fallback"

    async def _emit_skills_selected(
        self,
        record: RunRecord,
        matches: list[SkillMatch],
        *,
        confidence: float,
        strategy: str,
        reason: str,
        candidates: list[SkillMatch],
    ) -> None:
        primary = matches[0]
        display_names = [match.skill.display_name for match in matches]
        await self.events.emit(
            record.run_id,
            "skill_selected",
            record.phase,
            "success",
            " + ".join(display_names),
            reason,
            {
                # Keep the singular fields for stored runs and older clients.
                "skill": primary.skill.name,
                "description": primary.skill.description,
                "score": primary.score,
                "skills": [match.skill.name for match in matches],
                "display_names": display_names,
                "selections": [self._skill_candidate_payload(match) for match in matches],
                "confidence": confidence,
                "strategy": strategy,
                "matched_keywords": list(dict.fromkeys(
                    keyword for match in matches for keyword in match.matched_keywords
                )),
                "candidates": [self._skill_candidate_payload(item) for item in candidates],
            },
        )

    @staticmethod
    def _keyword_confidence(candidates: list[SkillMatch]) -> float:
        top = candidates[0].score
        second = candidates[1].score if len(candidates) > 1 else -1
        if top >= 2 and top > second:
            return 0.92
        if top > 0 and top > second:
            return 0.78
        if top > 0:
            return 0.55
        return 0.35

    @staticmethod
    def _skill_candidate_payload(match: SkillMatch) -> dict[str, Any]:
        return {
            "name": match.skill.name,
            "display_name": match.skill.display_name,
            "description": match.skill.description,
            "source": match.skill.source,
            "keyword_score": match.score,
            "matched_keywords": match.matched_keywords,
        }

    async def _interaction_first(self, record: RunRecord, workspace: Path) -> dict[str, Any] | None:
        previous: dict[str, Any] | None = None
        feedback = ""
        revision = 1
        product_context = _collect_product_context(workspace)
        await self.events.emit(
            record.run_id, "interaction_context_collected", "interaction_modeling", "success",
            "已读取产品需求", "根据任务和工作区需求文档建立终端用户交互模型。",
            {"source_files": product_context["source_files"]},
        )

        while revision <= 5:
            record.phase = "interaction_modeling"
            title, summary = PHASE_COPY[record.phase]
            await self.events.emit(
                record.run_id, "phase_changed", record.phase, "running", title, summary,
                {"revision": revision},
            )
            model = await self._generate_interaction_model(
                record.task,
                product_context["content"],
                feedback=feedback,
                previous=previous,
            )
            model_id = f"interaction_{uuid.uuid4().hex[:12]}"
            pending = {"model_id": model_id, "revision": revision, **model}
            await self.events.emit(
                record.run_id, "interaction_model_created", record.phase, "success",
                f"已生成交互流程 · 第 {revision} 版", model["summary"], pending,
            )

            decision_future = record.begin_interaction_confirmation(pending)
            record.status = "waiting_interaction_confirmation"
            record.phase = "interaction_confirmation"
            await self.events.emit(
                record.run_id, "interaction_confirmation_requested", record.phase, "pending",
                "这个交互流程符合你的预期吗？",
                "确认后 Agent 才会拆解实现计划并开始编写代码；也可以提出调整意见。",
                pending,
            )

            resolution = await decision_future
            record.clear_interaction_confirmation()
            decision = resolution.get("decision", "")
            if decision == "cancelled" or record.cancel_requested:
                return None

            approved = decision == "approve"
            record.status = "running"
            await self.events.emit(
                record.run_id, "interaction_confirmation_resolved", record.phase,
                "success" if approved else "info",
                "交互流程已确认" if approved else "已收到流程调整意见",
                "开始根据已确认流程实现产品。" if approved else resolution.get("feedback", "重新生成交互流程。"),
                {"model_id": model_id, "revision": revision, **resolution},
            )
            if not approved and resolution.get("feedback", "").strip():
                await self.events.emit(
                    record.run_id,
                    "user_requirement_received",
                    record.phase,
                    "info",
                    "收到补充需求",
                    resolution["feedback"].strip(),
                    {
                        "kind": "interaction_feedback",
                        "message": resolution["feedback"].strip(),
                        "model_id": model_id,
                        "revision": revision,
                        "status": "applied_to_next_model",
                    },
                )
            if approved:
                return model
            previous = model
            feedback = resolution.get("feedback", "")
            revision += 1

        raise RuntimeError("交互流程已修改 5 次仍未确认，请重新创建任务并明确需求")

    async def _generate_interaction_model(
        self,
        task: str,
        product_context: str,
        *,
        feedback: str = "",
        previous: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [{
            "role": "system",
            "content": (
                "你是产品交互建模模块。先从终端用户视角定义页面流转和内部核心状态，不编写代码。"
                "必须调用 submit_interaction_model，页面控制在 1–8 个；页面流转只保留关键用户路径，"
                "按入口到目标的主旅程顺序排列 pages，把弹窗视为节点但不要把按钮或字段误当成页面；"
                "页面名称保持简短唯一，purpose 只写一个用户目标；flow action 必须使用明确的动词和对象；"
                "合并意义相同的返回操作，禁止重复边，控制在 1–10 条；复杂流程保留一条主路径和必要分支，"
                "状态变化控制在 1–12 条。页面 flow 的 from/to 必须引用 pages 中的 id。"
            ),
        }, {
            "role": "user",
            "content": f"用户需求：\n{task}\n\n工作区需求资料：\n{product_context or '未发现额外需求文档。'}",
        }]
        if previous and feedback:
            messages.append({
                "role": "user",
                "content": (
                    f"上一版交互模型：\n{json.dumps(previous, ensure_ascii=False)}\n\n"
                    f"用户调整意见：\n{feedback}\n\n请生成完整的新版本，不要只描述差异。"
                ),
            })

        last_error = "模型没有提交交互流程"
        for _attempt in range(3):
            turn = await self.model.complete(messages, INTERACTION_MODEL_TOOL)
            messages.append(turn.assistant_message)
            target_call = next((call for call in turn.tool_calls if call.name == "submit_interaction_model"), None)
            if target_call and not target_call.argument_error:
                try:
                    return _normalize_interaction_model(target_call.arguments)
                except ValueError as exc:
                    last_error = str(exc)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": target_call.call_id,
                        "name": target_call.name,
                        "content": json.dumps({"ok": False, "error": last_error}, ensure_ascii=False),
                    })
                    continue
            if target_call and target_call.argument_error:
                last_error = target_call.argument_error
                messages.append({
                    "role": "tool",
                    "tool_call_id": target_call.call_id,
                    "name": target_call.name,
                    "content": json.dumps({"ok": False, "error": last_error}, ensure_ascii=False),
                })
            else:
                messages.append({
                    "role": "user",
                    "content": "请调用 submit_interaction_model 提交结构化页面流转和状态机，不要只输出自然语言。",
                })
        raise RuntimeError(f"无法生成有效的产品交互流程：{last_error}")

    async def _set_phase(self, record: RunRecord, next_phase: str) -> None:
        if record.phase == next_phase or next_phase not in PHASE_COPY:
            return
        record.phase = next_phase
        title, summary = PHASE_COPY[next_phase]
        await self.events.emit(
            record.run_id, "phase_changed", record.phase, "running", title, summary,
        )

    async def _request_approval(
        self,
        record: RunRecord,
        registry: ToolRegistry,
        tool_name: str,
        arguments: dict[str, Any],
        blocked_result: ToolResult,
    ) -> ToolResult | None:
        approval_id = f"approval_{uuid.uuid4().hex[:12]}"
        approval = {
            "approval_id": approval_id,
            "tool": tool_name,
            "arguments": _safe_arguments(arguments),
            "reason": str(blocked_result.data.get("permission_reason") or blocked_result.error or "该操作需要额外权限"),
            "risk": str(blocked_result.data.get("risk", "medium")),
            "scope": "exact_action_once",
        }
        decision_future = record.begin_approval(approval)
        record.status = "waiting_approval"
        await self.events.emit(
            record.run_id, "approval_requested", record.phase, "pending", "需要你的授权",
            approval["reason"], approval,
        )

        decision = await decision_future
        record.clear_approval()
        if decision == "cancelled" or record.cancel_requested:
            return None

        allowed = decision == "allow"
        record.status = "running"
        await self.events.emit(
            record.run_id, "approval_resolved", record.phase, "success" if allowed else "failed",
            "已允许本次操作" if allowed else "已拒绝本次操作",
            "Agent 将执行刚才展示的操作并继续任务。" if allowed else "Agent 会收到拒绝结果并尝试其他方案。",
            {**approval, "decision": decision},
        )
        if not allowed:
            return ToolResult(
                False,
                f"用户拒绝授权 {tool_name}",
                data={"user_denied": True, "approval_id": approval_id},
                error="用户拒绝了本次操作；请调整方案，不要重复请求相同操作",
            )
        return await registry.execute(tool_name, arguments, approved=True)

    async def _emit_plan(self, record: RunRecord, plan: "PlanTracker") -> None:
        await self.events.emit(
            record.run_id, "plan_updated", record.phase, "info", "计划已更新",
            plan.progress_text, {"items": plan.items},
        )

    async def _finish(self, record: RunRecord, status: str, summary: str, plan: "PlanTracker | None" = None) -> None:
        if self.events.get(record.run_id) and self.events.get(record.run_id).terminal:
            return
        record.status = status
        record.phase = "completed" if status == "completed" else status
        if status == "completed" and record.user_guide_path and record.user_guide_path not in summary:
            summary = f"{summary} 使用说明：{record.user_guide_path}。"
        record.summary = summary
        if plan and status == "completed":
            plan.complete_all()
            await self._emit_plan(record, plan)
        structured_summary = _structured_run_summary(record, plan, status, summary)
        if self.session_store and record.session_entry_id:
            self.session_store.update_entry(
                record.session_entry_id,
                status=status,
                summary=summary,
                changed_files=record.changed_files,
                successful_commands=record.successful_commands,
                structured_summary=structured_summary,
            )
        await self.events.emit(
            record.run_id, "run_finished", record.phase, "success" if status == "completed" else "failed",
            "任务完成" if status == "completed" else "任务已停止", summary,
            {
                "status": status,
                "changed_files": record.changed_files,
                "read_files": record.read_files,
                "successful_commands": record.successful_commands,
                "traceability": record.traceability,
                "user_guide_path": record.user_guide_path,
                "steering_messages": record.steering_messages,
            },
        )

    @staticmethod
    def _initial_messages(
        record: RunRecord,
        matches: list[SkillMatch] | SkillMatch,
        workspace: Path,
        interaction_model: dict[str, Any] | None = None,
        settings: AgentSettings | None = None,
    ) -> list[dict[str, Any]]:
        settings = settings or AgentSettings()
        # Keep this helper compatible with older integrations that passed one
        # match directly while the runtime now always supplies a composition.
        if isinstance(matches, SkillMatch):
            matches = [matches]
        interaction_guidance = ""
        requires_user_guide = _requires_user_guide(record, workspace, matches, interaction_model)
        if interaction_model:
            interaction_guidance = (
                "\n\n用户已经确认以下产品交互模型。实现必须覆盖其中的页面、流转、状态和验收标准；"
                "完成前要逐项核对，不能擅自改变已确认的核心交互。"
                "创建、修改、读取审查或运行验证时，使用 requirement_ids 标注本次操作直接覆盖的 AC 编号；"
                "不能把一次无关命令成功当作全部需求的验证：\n"
                f"{json.dumps(interaction_model, ensure_ascii=False, indent=2)}"
            )
        skill_sections = "\n".join(
            f"- {match.skill.name}（{match.skill.display_name}）：{match.skill.description}"
            for match in matches
        )
        skill_names = "、".join(match.skill.display_name for match in matches)
        delivery_guidance = ""
        if requires_user_guide:
            delivery_guidance = """

本轮面向非开发人员交付完整产品。实现稳定后、最终验证前必须创建或更新工作区根目录 README.md，并在 finish 前重新读取检查。README 使用通俗语言，至少设置清晰章节说明：项目简介/主要功能、环境准备、启动方法、使用说明、常见问题或注意事项。启动命令必须来自真实项目配置且可直接复制；不得写入 API Key、令牌或臆测功能。"""
        system = f"""你是 IntentFlow 的决策模块。你不能直接访问文件或终端，只能调用提供的本地工具。

安全与执行规则：
1. 先观察再修改，保持改动聚焦，禁止访问工作区外路径。
2. 工具失败是新的观察：阅读错误、调整参数或换一种方法，不要机械重复。
3. 修改代码后必须运行测试或等价验证。
4. 完成时必须调用 finish；无法继续也要在 summary 中说明事实。
5. 不输出隐藏思维过程。工具调用前只需要选择下一项可验证行动。
6. 工具列表中标注为 Skill 外的操作会暂停并请求用户单次授权；确有必要时应直接调用对应工具，不要用命令绕过权限。
7. 宿主设置了质量关卡：修复类任务修改前要建立基线；修改后要验证；首次 finish 会进入自检，读取改动后才能最终完成。{delivery_guidance}

本轮已组合 Skill：{skill_names}
常驻上下文只提供以下 Skill 元数据：
{skill_sections}{interaction_guidance}

需要使用某项 Skill 的专门流程时，先调用 load_skill 读取它的完整说明；不要凭描述猜测未加载的细节。
多项 Skill 同时生效时，应按需逐项加载并综合其目标，但以用户任务为准；重复规则只执行一次，不做无关扩展。

Agent 配置：模式={settings.mode}；强制验证={'是' if settings.require_verification else '否'}；完成前自检={'是' if settings.require_review else '否'}。
工作区：{workspace.name}
最大步骤：由宿主程序控制。"""
        messages = [{"role": "system", "content": system}]
        if record.continuation_context:
            continuation = json.dumps(record.continuation_context, ensure_ascii=False, indent=2)
            messages.extend([
                {"role": "user", "content": f"原始任务：{record.root_task}"},
                {
                    "role": "assistant",
                    "content": (
                        "以下是宿主程序从同一工作区上一轮运行中恢复的事实摘要。"
                        "必须先重新观察当前文件状态再继续，不能假设一次性授权仍然有效：\n"
                        f"{continuation}"
                    ),
                },
                {"role": "user", "content": f"当前补充要求：{record.task}"},
            ])
        else:
            messages.append({"role": "user", "content": record.task})
        return messages


class PlanTracker:
    def __init__(self, items: list[dict[str, Any]], requirement_ids: list[str] | None = None) -> None:
        self.items = deepcopy(items)
        requirement_ids = list(requirement_ids or [])
        if requirement_ids:
            for item in self.items:
                if item.get("id") in {"diagnose", "edit", "verify", "review"}:
                    item["covers"] = list(requirement_ids)

    @property
    def progress_text(self) -> str:
        done = sum(item["status"] == "success" for item in self.items)
        return f"{done}/{len(self.items)} 个步骤已完成"

    def on_tool_started(
        self,
        name: str,
        has_changes: bool,
        *,
        baseline_observed: bool = False,
        review_requested: bool = False,
    ) -> None:
        if name in {"list_files", "read_file", "search_text"}:
            if review_requested and has_changes:
                self._running("review")
            elif baseline_observed:
                self._running("diagnose")
            else:
                self._running("inspect")
        elif name in {"create_file", "apply_patch"}:
            self._done("inspect")
            self._done("baseline")
            self._done("diagnose")
            self._running("edit")
        elif name == "run_command":
            if has_changes:
                self._done("edit")
                self._running("verify")
            else:
                self._done("inspect")
                self._running("baseline")

    def on_tool_finished(
        self,
        name: str,
        ok: bool,
        has_changes: bool,
        *,
        baseline_observed: bool = False,
        review_requested: bool = False,
        review_evidence: bool = False,
    ) -> None:
        if name in {"list_files", "read_file", "search_text"} and ok:
            if review_requested and has_changes:
                if review_evidence:
                    self._done("review")
                else:
                    self._running("review")
            else:
                self._done("inspect")
                self._running("diagnose" if baseline_observed else "baseline")
        elif name in {"create_file", "apply_patch"} and ok:
            self._done("edit")
            self._running("verify")
        elif name == "run_command":
            if not has_changes and baseline_observed:
                self._done("baseline")
                self._running("diagnose")
            elif ok and has_changes:
                self._done("verify")
                self._running("review")

    def require(self, item_id: str) -> None:
        self._running(item_id)

    def complete(self, item_id: str) -> None:
        self._done(item_id)

    def complete_all(self) -> None:
        for item in self.items:
            item["status"] = "success"

    def _running(self, item_id: str) -> None:
        for item in self.items:
            if item["id"] == item_id and item["status"] != "success":
                item["status"] = "running"
            elif item["status"] == "running" and item["id"] != item_id:
                item["status"] = "pending"

    def _done(self, item_id: str) -> None:
        for item in self.items:
            if item["id"] == item_id:
                item["status"] = "success"

    def _is_done(self, item_id: str) -> bool:
        return any(item["id"] == item_id and item["status"] == "success" for item in self.items)


def _tool_summary(name: str, arguments: dict[str, Any]) -> str:
    if name == "load_skill":
        return f"按需读取：{arguments.get('skill_name', '')}"
    if name in {"read_file", "list_files", "create_file", "apply_patch"}:
        return str(arguments.get("path", "."))
    if name == "search_text":
        return f"搜索：{arguments.get('query', '')}"
    if name == "run_command":
        return str(arguments.get("command", ""))
    if name == "finish":
        return "整理修改和验证结果"
    return "执行模型请求的本地动作"


def _failure_count_text(count: int) -> str:
    return {1: "一次", 2: "两次", 3: "三次"}.get(count, f" {count} 次")


def _phase_for_tool(
    name: str,
    *,
    has_changes: bool,
    baseline_observed: bool,
    review_requested: bool,
) -> str:
    if name in {"list_files", "read_file", "search_text"}:
        if review_requested and has_changes:
            return "reviewing"
        return "diagnosing" if baseline_observed else "inspecting"
    if name in {"create_file", "apply_patch"}:
        return "implementing"
    if name == "run_command":
        if review_requested:
            return "reviewing"
        return "verifying" if has_changes else "reproducing"
    return "reviewing" if review_requested else "diagnosing"


def _quality_checkpoint(
    kind: str,
    summary: str,
    instruction: str,
    *,
    review_path: str = "",
    traceability: dict[str, Any] | None = None,
    gaps: list[dict[str, str]] | None = None,
) -> ToolResult:
    data: dict[str, Any] = {
        "checkpoint_required": True,
        "checkpoint_kind": kind,
        "instruction": instruction,
    }
    if review_path:
        data["review_path"] = review_path
    if traceability:
        data["traceability"] = traceability
    if gaps:
        data["gaps"] = gaps
    return ToolResult(False, summary, data=data, error=instruction)


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    safe = deepcopy(arguments)
    for key in list(safe):
        if any(token in key.lower() for token in ("key", "token", "secret", "password")):
            safe[key] = "[REDACTED]"
    for key in ("old_text", "new_text", "content"):
        if isinstance(safe.get(key), str) and len(safe[key]) > 2_000:
            safe[key] = safe[key][:2_000] + "\n... 已截断 ..."
    return safe


def _combined_skill_tools(matches: list[SkillMatch]) -> list[str]:
    """Merge selected Skill permissions without changing host safety rules."""
    allowed = {
        tool
        for match in matches
        for tool in match.skill.allowed_tools
    }
    preferred_order = (
        "list_files", "read_file", "search_text", "create_file",
        "apply_patch", "run_command", "finish",
    )
    return [tool for tool in preferred_order if tool in allowed]


def _with_user_guide_plan(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose the mandatory end-user handoff as a first-class plan node."""
    plan = deepcopy(items)
    if any(item.get("id") == "guide" for item in plan):
        return plan
    guide = {"id": "guide", "title": "编写并检查终端用户使用说明", "status": "pending"}
    review_index = next((index for index, item in enumerate(plan) if item.get("id") == "review"), len(plan))
    plan.insert(review_index, guide)
    return plan


def _requires_user_guide(
    record: RunRecord,
    workspace: Path,
    matches: list[SkillMatch],
    interaction_model: dict[str, Any] | None,
) -> bool:
    should_model, reason_code, _reason = _should_create_interaction_model(record, workspace)
    product_delivery = should_model and reason_code in {
        "greenfield_product_build",
        "requirements_backed_build",
    }
    return product_delivery and (
        bool(interaction_model)
        or any(match.skill.name == "frontend_build" for match in matches)
    )


def _is_user_guide_call(name: str, arguments: dict[str, Any]) -> bool:
    if name not in {"create_file", "apply_patch", "read_file"}:
        return False
    path = str(arguments.get("path", "")).replace("\\", "/").removeprefix("./").casefold()
    return path in {"readme.md", "readme.txt"}


def _should_create_interaction_model(record: RunRecord, workspace: Path) -> tuple[bool, str, str]:
    combined_task = f"{record.root_task}\n{record.task}".casefold()
    if any(signal.casefold() in combined_task for signal in INTERACTION_EXPLICIT_SIGNALS):
        return True, "explicit_request", "任务明确要求交互流程图，因此先建模并等待确认。"

    if any(signal.casefold() in combined_task for signal in INTERACTION_MAINTENANCE_SIGNALS):
        return False, "maintenance_task", "这是修复、解释或局部调整任务，不需要重新生成产品流程图。"

    implementation_names = {
        "package.json", "index.html", "vite.config.js", "vite.config.ts", "next.config.js", "next.config.ts",
        "src", "app", "pages", "public", "server.js", "server.ts",
    }
    requirement_names = {
        "requirements.md", "requirement.md", "task.md", "spec.md", "prd.md", "需求文档.md",
    }
    try:
        entries = [
            entry for entry in workspace.iterdir()
            if entry.name not in {".intentflow", ".tracecoder", "node_modules"}
        ]
        source_suffixes = {".html", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".py"}
        has_implementation = any(
            (entry.is_file() and (
                entry.name.casefold() in implementation_names
                or entry.suffix.casefold() in source_suffixes
            ))
            or (entry.is_dir() and entry.name.casefold() in implementation_names and any(
                child.is_file() and child.suffix.casefold() in source_suffixes
                for child in entry.rglob("*")
            ))
            for entry in entries
        )
        has_requirements = any(entry.name.casefold() in requirement_names for entry in entries if entry.is_file())
    except OSError:
        has_implementation = True
        has_requirements = False
    if has_implementation:
        return False, "existing_implementation", "工作区已经包含实现文件，本轮按现有项目任务直接分析和修改。"

    if any(signal.casefold() in combined_task for signal in INTERACTION_BUILD_SIGNALS):
        return True, "greenfield_product_build", "检测到空白或仅含需求资料的工作区，以及完整产品构建意图。"

    if has_requirements and any(
        signal.casefold() in combined_task for signal in INTERACTION_REQUIREMENTS_TASK_SIGNALS
    ):
        return True, "requirements_backed_build", "用户描述较简略，但工作区含需求文档且要求继续实现，先根据文档生成交互流程供确认。"

    return False, "no_product_build_intent", "任务和工作区证据不足以确认是在新建终端产品，因此不贸然生成流程图。"


def _collect_product_context(workspace: Path, max_chars: int = 18_000) -> dict[str, Any]:
    """Collect a small, predictable product brief without scanning source code."""
    preferred_names = (
        "REQUIREMENTS.md",
        "REQUIREMENT.md",
        "TASK.md",
        "SPEC.md",
        "README.md",
        "README.txt",
    )
    source_files: list[str] = []
    sections: list[str] = []
    remaining = max_chars
    for name in preferred_names:
        path = workspace / name
        if not path.is_file() or remaining <= 0:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        excerpt = content[:remaining]
        source_files.append(name)
        sections.append(f"--- {name} ---\n{excerpt}")
        remaining -= len(excerpt)

    try:
        top_level = sorted(entry.name for entry in workspace.iterdir())[:80]
    except OSError:
        top_level = []
    directory_note = "工作区顶层文件：" + ("、".join(top_level) if top_level else "（空）")
    content = "\n\n".join([directory_note, *sections])
    return {"source_files": source_files, "content": content}


def _normalize_interaction_model(arguments: dict[str, Any]) -> dict[str, Any]:
    def clean(value: Any, field: str, max_length: int = 160) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} 必须是非空字符串")
        return value.strip()[:max_length]

    title = clean(arguments.get("title"), "title", 80)
    summary = clean(arguments.get("summary"), "summary", 240)

    raw_pages = arguments.get("pages")
    if not isinstance(raw_pages, list) or not 1 <= len(raw_pages) <= 8:
        raise ValueError("pages 必须包含 1–8 个页面")
    pages: list[dict[str, str]] = []
    page_ids: set[str] = set()
    for index, item in enumerate(raw_pages, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"pages[{index}] 必须是对象")
        page_id = clean(item.get("id"), f"pages[{index}].id", 48)
        if page_id in page_ids:
            raise ValueError(f"页面 id 重复：{page_id}")
        page_ids.add(page_id)
        pages.append({
            "id": page_id,
            "name": clean(item.get("name"), f"pages[{index}].name", 80),
            "purpose": clean(item.get("purpose"), f"pages[{index}].purpose", 200),
        })

    raw_flows = arguments.get("flows")
    if not isinstance(raw_flows, list) or not 1 <= len(raw_flows) <= 12:
        raise ValueError("flows 必须包含 1–12 条页面流转")
    flows: list[dict[str, str]] = []
    flow_keys: set[tuple[str, str, str]] = set()
    for index, item in enumerate(raw_flows, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"flows[{index}] 必须是对象")
        source = clean(item.get("from"), f"flows[{index}].from", 48)
        target = clean(item.get("to"), f"flows[{index}].to", 48)
        if source not in page_ids or target not in page_ids:
            raise ValueError(f"flows[{index}] 引用了不存在的页面 id")
        action = clean(item.get("action"), f"flows[{index}].action", 120)
        flow_key = (source, action, target)
        if flow_key in flow_keys:
            raise ValueError(f"flows[{index}] 与前面的页面流转重复")
        flow_keys.add(flow_key)
        flows.append({
            "from": source,
            "action": action,
            "to": target,
        })

    raw_states = arguments.get("states")
    if not isinstance(raw_states, list) or not 1 <= len(raw_states) <= 12:
        raise ValueError("states 必须包含 1–12 条状态变化")
    states: list[dict[str, str]] = []
    for index, item in enumerate(raw_states, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"states[{index}] 必须是对象")
        states.append({
            "from": clean(item.get("from"), f"states[{index}].from", 80),
            "event": clean(item.get("event"), f"states[{index}].event", 120),
            "to": clean(item.get("to"), f"states[{index}].to", 80),
        })

    raw_criteria = arguments.get("acceptance_criteria")
    if not isinstance(raw_criteria, list) or not 1 <= len(raw_criteria) <= 10:
        raise ValueError("acceptance_criteria 必须包含 1–10 条标准")
    criteria: list[dict[str, str]] = []
    for index, item in enumerate(raw_criteria, start=1):
        if isinstance(item, dict):
            description = clean(item.get("description"), f"acceptance_criteria[{index}].description", 240)
            priority = str(item.get("priority", "must")).strip()
            verification = str(item.get("verification", "automated_test")).strip()
        else:  # Backward-compatible with stored runs and deterministic test models.
            description = clean(item, f"acceptance_criteria[{index}]", 240)
            priority = "must"
            verification = "automated_test"
        if priority not in {"must", "should"}:
            raise ValueError(f"acceptance_criteria[{index}].priority 必须是 must 或 should")
        if verification not in {"automated_test", "build_check", "human_review"}:
            raise ValueError(f"acceptance_criteria[{index}].verification 类型不支持")
        criteria.append({
            "id": f"AC-{index:02d}",
            "description": description,
            "priority": priority,
            "verification": verification,
        })

    return {
        "title": title,
        "summary": summary,
        "pages": pages,
        "flows": flows,
        "states": states,
        "acceptance_criteria": criteria,
    }


def _compact_context(
    messages: list[dict[str, Any]],
    max_chars: int = 48_000,
    *,
    goal: str = "",
    current_request: str = "",
    plan_items: list[dict[str, Any]] | None = None,
    changed_files: list[str] | None = None,
    successful_commands: list[str] | None = None,
    traceability: dict[str, Any] | None = None,
    steering_messages: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    serialized_length = sum(len(json.dumps(message, ensure_ascii=False)) for message in messages)
    if serialized_length <= max_chars or len(messages) <= 12:
        return messages, None

    read_files: list[str] = []
    recent_errors: list[str] = []
    for message in messages:
        if message.get("role") != "tool":
            continue
        try:
            result = json.loads(str(message.get("content") or "{}"))
        except json.JSONDecodeError:
            continue
        if message.get("name") == "read_file":
            path = str(result.get("data", {}).get("path", "")).strip()
            if path and path not in read_files:
                read_files.append(path)
        error = str(result.get("error") or "").strip()
        if error:
            recent_errors.append(error[:500])

    progress = {"done": [], "in_progress": [], "pending": []}
    status_bucket = {"success": "done", "running": "in_progress", "pending": "pending"}
    for item in plan_items or []:
        bucket = status_bucket.get(str(item.get("status", "pending")), "pending")
        title = str(item.get("title") or item.get("label") or item.get("id") or "").strip()
        if title:
            progress[bucket].append(title)
    structured = {
        "goal": goal,
        "current_request": current_request,
        "progress": progress,
        "files": {
            "read": read_files[-30:],
            "modified": list(dict.fromkeys(changed_files or [])),
        },
        "successful_commands": list(dict.fromkeys(successful_commands or []))[-20:],
        "recent_errors": recent_errors[-5:],
        "traceability": traceability,
        "steering": [
            {
                "steering_id": str(item.get("steering_id", "")),
                "message": str(item.get("message", "")),
                "status": str(item.get("status", "")),
            }
            for item in (steering_messages or [])[-20:]
        ],
        "compacted_message_count": 0,
    }

    prefix = messages[:1]
    recent = list(messages[-16:])
    while recent and recent[0].get("role") == "tool":
        recent.pop(0)
    structured["compacted_message_count"] = max(0, len(messages) - len(prefix) - len(recent))
    compacted = prefix + [{
        "role": "system",
        "content": (
            "以下是宿主对较早上下文生成的结构化事实。它是工作记忆，不代表任务已经完成；"
            "继续前应按需重新读取当前文件状态。\n"
            + json.dumps(structured, ensure_ascii=False, indent=2)
        ),
    }] + recent
    return compacted, structured


def _structured_run_summary(
    record: RunRecord,
    plan: PlanTracker | None,
    status: str,
    summary: str,
) -> dict[str, Any]:
    progress = {"done": [], "in_progress": [], "pending": []}
    status_bucket = {"success": "done", "running": "in_progress", "pending": "pending"}
    for item in plan.items if plan else []:
        bucket = status_bucket.get(str(item.get("status", "pending")), "pending")
        title = str(item.get("title") or item.get("label") or item.get("id") or "").strip()
        if title:
            progress[bucket].append(title)
    previous = record.continuation_context or {}
    previous_structured = previous.get("structured_session_summary")
    previous_structured = previous_structured if isinstance(previous_structured, dict) else {}
    previous_files = previous_structured.get("files")
    previous_files = previous_files if isinstance(previous_files, dict) else {}
    read_files = list(dict.fromkeys([
        *[str(value) for value in previous_files.get("read", [])],
        *record.read_files,
    ]))
    modified_files = list(dict.fromkeys([
        *[str(value) for value in previous_files.get("modified", [])],
        *[str(value) for value in previous.get("changed_files", [])],
        *record.changed_files,
    ]))
    commands = list(dict.fromkeys([
        *[str(value) for value in previous_structured.get("successful_commands", [])],
        *[str(value) for value in previous.get("successful_commands", [])],
        *record.successful_commands,
    ]))
    result = {
        "goal": record.root_task,
        "current_request": record.task,
        "status": status,
        "summary": summary,
        "progress": progress,
        "files": {"read": read_files, "modified": modified_files},
        "successful_commands": commands,
        "traceability": record.traceability,
        "user_guide_path": record.user_guide_path,
        "steering": [dict(item) for item in record.steering_messages],
    }
    if isinstance(previous_structured.get("progress"), dict):
        result["previous_progress"] = previous_structured["progress"]
    return result
