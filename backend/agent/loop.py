from __future__ import annotations

import asyncio
import json
import math
import os
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from backend.events.store import EventStore
from backend.llm.client import ModelClient
from backend.settings import AgentSettings, AgentSettingsStore
from backend.skills.router import SkillMatch, SkillRouter
from backend.state import RunRecord
from backend.tools.registry import ToolRegistry, ToolResult


TOOL_TITLES = {
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
    "verifying": ("正在验证修改", "运行验证命令检查任务结果。"),
    "reviewing": ("正在完成前自检", "重新审查改动文件，检查需求覆盖和潜在副作用。"),
}

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
                            "name": {"type": "string"},
                            "purpose": {"type": "string"},
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
                            "action": {"type": "string", "description": "用户操作"},
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
                    "items": {"type": "string"},
                },
            },
            "required": ["title", "summary", "pages", "flows", "states", "acceptance_criteria"],
            "additionalProperties": False,
        },
    },
}]

SKILL_CONFIRMATION_THRESHOLD = 0.68


class AgentLoop:
    def __init__(
        self,
        events: EventStore,
        skill_router: SkillRouter,
        model: ModelClient,
        max_steps: int | None = None,
        settings_store: AgentSettingsStore | None = None,
    ) -> None:
        self.events = events
        self.skill_router = skill_router
        self.model = model
        self.max_steps_override = max_steps
        self.environment_max_steps = int(os.environ["TRACECODER_MAX_STEPS"]) if "TRACECODER_MAX_STEPS" in os.environ else None
        self.settings_store = settings_store

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
        max_steps = self.max_steps_override or self.environment_max_steps or settings.max_steps
        failure_limit = settings.failure_limit
        record.status = "running"
        record.phase = "selecting_skill"
        await self.events.emit(
            record.run_id, "run_started", record.phase, "running", "任务已启动",
            f"工作区：{record.workspace}", {
                "task": record.task,
                "workspace": record.workspace,
                "mode": self.model.mode_name,
                "agent_settings": settings.public_dict(),
            },
        )

        match = await self._select_skill(record)
        if match is None:
            await self._finish(record, "cancelled", "任务在等待 Skill 选择时被用户停止。")
            return

        interaction_model: dict[str, Any] | None = None
        if match.skill.name == "frontend_build" and settings.interaction_first:
            interaction_model = await self._interaction_first(record, workspace)
            if interaction_model is None:
                await self._finish(record, "cancelled", "任务在等待产品交互确认时被用户停止。")
                return

        record.phase = "planning"
        await self.events.emit(
            record.run_id, "phase_changed", record.phase, "running", "正在制定计划",
            "根据 Skill 策略建立可追踪的执行步骤。",
        )
        plan = PlanTracker(match.skill.plan)
        await self._emit_plan(record, plan)

        registry = ToolRegistry(
            workspace,
            match.skill.allowed_tools,
            agent_mode=settings.mode,
            max_command_timeout=settings.command_timeout,
        )
        messages = self._initial_messages(record, match, workspace, interaction_model, settings)
        record.phase = "inspecting"
        await self.events.emit(
            record.run_id, "phase_changed", record.phase, "running", "进入执行阶段",
            "先理解工作区，再建立基线、定位、修改、验证并自检。",
        )

        recent_fingerprints: list[str] = []
        consecutive_failures = 0
        no_tool_turns = 0
        inspection_observed = False
        baseline_observed = False
        verification_after_change = False
        review_requested = False
        review_evidence = False
        requires_baseline = match.skill.name in {"bug_fix", "test_writer"}
        step_delay = float(os.getenv("TRACECODER_STEP_DELAY", "0.2" if self.model.mode_name == "demo" else "0"))

        for _step in range(1, max_steps + 1):
            if record.cancel_requested:
                await self._finish(record, "cancelled", "任务已被用户停止。")
                return

            messages = _compact_context(messages, max_chars=settings.context_budget)
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

                fingerprint = json.dumps({"name": call.name, "arguments": call.arguments}, ensure_ascii=False, sort_keys=True)
                repeated = len(recent_fingerprints) >= 2 and recent_fingerprints[-2:] == [fingerprint, fingerprint]
                recent_fingerprints.append(fingerprint)
                recent_fingerprints = recent_fingerprints[-4:]

                preflight_checkpoint = _preflight_checkpoint(
                    call.name,
                    inspection_observed=inspection_observed,
                    baseline_observed=baseline_observed,
                    requires_baseline=requires_baseline,
                )
                if preflight_checkpoint:
                    checkpoint_item = str(preflight_checkpoint.data["checkpoint_kind"])
                    plan.require(checkpoint_item)
                    next_phase = "inspecting" if checkpoint_item == "inspect" else "reproducing"
                else:
                    plan.on_tool_started(
                        call.name,
                        bool(record.changed_files),
                        baseline_observed=baseline_observed,
                        review_requested=review_requested,
                    )
                    next_phase = record.phase if call.name == "finish" else _phase_for_tool(
                        call.name,
                        has_changes=bool(record.changed_files),
                        baseline_observed=baseline_observed,
                        review_requested=review_requested,
                    )
                await self._set_phase(record, next_phase)
                await self._emit_plan(record, plan)

                title = TOOL_TITLES.get(call.name, call.name)
                await self.events.emit(
                    record.run_id, "tool_started", record.phase, "running", title,
                    _tool_summary(call.name, call.arguments),
                    {"tool": call.name, "arguments": _safe_arguments(call.arguments)},
                )

                if call.argument_error:
                    result = ToolResult(False, f"{call.name} 参数解析失败", error=call.argument_error)
                elif repeated:
                    result = ToolResult(False, "检测到重复工具调用", error="同一动作连续出现三次，已阻止执行；请换一种方法")
                elif preflight_checkpoint:
                    result = preflight_checkpoint
                else:
                    result = await registry.execute(call.name, call.arguments)

                if result.approval_required:
                    result = await self._request_approval(record, registry, call.name, call.arguments, result)
                    if result is None:
                        await self._finish(record, "cancelled", "任务在等待授权时被用户停止。", plan)
                        return

                if call.name == "finish" and result.ok:
                    if settings.require_verification and record.changed_files and not verification_after_change:
                        result = _quality_checkpoint(
                            "verify",
                            "修改后还没有成功的验证结果",
                            "请运行与任务对应的测试或检查命令；验证通过后再调用 finish。",
                        )
                        plan.require("verify")
                        await self._set_phase(record, "verifying")
                    elif settings.require_review and record.changed_files and not review_requested:
                        review_requested = True
                        result = _quality_checkpoint(
                            "review",
                            "进入完成前自检",
                            f"请重新读取至少一个改动文件，优先检查 {record.changed_files[-1]}，确认没有无关修改或遗漏。",
                            review_path=record.changed_files[-1],
                        )
                        plan.require("review")
                        await self._set_phase(record, "reviewing")
                    elif settings.require_review and record.changed_files and not review_evidence:
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

                payload = {"tool": call.name, "arguments": _safe_arguments(call.arguments), **result.data}
                if result.error:
                    payload["error"] = result.error
                await self.events.emit(
                    record.run_id, "tool_finished", record.phase,
                    "success" if result.ok else "info" if result.data.get("checkpoint_required") else "failed",
                    title, result.summary, payload,
                )

                if call.name in {"create_file", "apply_patch"} and result.ok:
                    changed_path = str(result.data.get("path", ""))
                    if changed_path and changed_path not in record.changed_files:
                        record.changed_files.append(changed_path)
                    await self.events.emit(
                        record.run_id, "file_changed", record.phase, "success",
                        f"文件{'已创建' if call.name == 'create_file' else '已修改'} · {changed_path}",
                        "新文件内容可在 Diff 标签中审查。" if call.name == "create_file" else "局部补丁已应用，可在 Diff 标签中审查。",
                        result.data,
                    )
                    verification_after_change = False
                    review_requested = False
                    review_evidence = False
                if call.name == "run_command" and "exit_code" in result.data and not record.changed_files:
                    baseline_observed = True
                if call.name == "run_command" and result.ok:
                    command = str(result.data.get("command", ""))
                    if command:
                        record.successful_commands.append(command)
                    if record.changed_files:
                        verification_after_change = True
                if call.name in {"list_files", "read_file", "search_text"} and result.ok:
                    inspection_observed = True
                if review_requested and result.ok and _is_review_action(call.name, call.arguments, record.changed_files):
                    review_evidence = True

                plan.on_tool_finished(
                    call.name,
                    result.ok,
                    bool(record.changed_files),
                    baseline_observed=baseline_observed,
                    review_requested=review_requested,
                    review_evidence=review_evidence,
                )
                await self._emit_plan(record, plan)

                messages.append({
                    "role": "tool",
                    "tool_call_id": call.call_id,
                    "name": call.name,
                    "content": json.dumps(result.to_dict(), ensure_ascii=False),
                })

                if call.name == "finish" and result.ok:
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

    async def _select_skill(self, record: RunRecord) -> SkillMatch | None:
        if record.requested_skill != "auto":
            match = self.skill_router.match_enabled(record.requested_skill, record.task)
            await self._emit_skill_selected(
                record,
                match,
                confidence=1.0,
                strategy="manual",
                reason="用户在任务开始前手动指定了该 Skill。",
                candidates=[match],
            )
            return match

        ranked = self.skill_router.rank(record.task)
        candidates = ranked[:3] if ranked[0].score > 0 else ranked
        candidate_payloads = [self._skill_candidate_payload(item) for item in candidates]
        await self.events.emit(
            record.run_id,
            "skill_candidates",
            record.phase,
            "success",
            "已生成 Skill 候选",
            "先按触发词筛选候选，再结合任务语义决定最终能力。",
            {"candidates": candidate_payloads},
        )

        selected, confidence, reason, strategy = await self._semantic_skill_choice(record.task, candidates)
        if confidence < SKILL_CONFIRMATION_THRESHOLD and len(candidates) > 1:
            selection = {
                "selection_id": f"skill_{uuid.uuid4().hex[:12]}",
                "recommended": selected.skill.name,
                "confidence": confidence,
                "reason": reason,
                "candidates": candidate_payloads,
            }
            future = record.begin_skill_confirmation(selection)
            record.status = "waiting_skill_confirmation"
            await self.events.emit(
                record.run_id,
                "skill_confirmation_requested",
                record.phase,
                "pending",
                "需要确认 Skill",
                f"自动路由置信度为 {round(confidence * 100)}%，请选择最符合任务的能力。",
                selection,
            )
            chosen_name = await future
            record.clear_skill_confirmation()
            if chosen_name == "cancelled" or record.cancel_requested:
                return None
            selected = self.skill_router.match_enabled(chosen_name, record.task)
            confidence = 1.0
            reason = "用户从候选列表中确认了该 Skill。"
            strategy = "user_confirmed"
            record.status = "running"
            await self.events.emit(
                record.run_id,
                "skill_confirmation_resolved",
                record.phase,
                "success",
                "Skill 已确认",
                selected.skill.display_name,
                {"selection_id": selection["selection_id"], "skill": selected.skill.name},
            )

        await self._emit_skill_selected(
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
    ) -> tuple[SkillMatch, float, str, str]:
        fallback = candidates[0]
        fallback_confidence = self._keyword_confidence(candidates)
        fallback_reason = f"关键词路由：{fallback.reason}。"
        if self.model.mode_name != "model":
            return fallback, fallback_confidence, fallback_reason, "keyword"

        names = [item.skill.name for item in candidates]
        tool = [{
            "type": "function",
            "function": {
                "name": "select_skill",
                "description": "从候选 Skill 中选择最适合当前任务的一项，并给出置信度和简短理由。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name": {"type": "string", "enum": names},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "reason": {"type": "string"},
                    },
                    "required": ["skill_name", "confidence", "reason"],
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
                    "你是编程智能体的 Skill 路由器。只能从候选项中选择一个最适合完成用户任务的 Skill。"
                    "结合完整任务语义、Skill 描述和关键词得分判断；不要执行任务，只调用 select_skill。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"task": task, "candidates": candidate_context}, ensure_ascii=False),
            },
        ]
        try:
            turn = await self.model.complete(messages, tool)
            call = next((item for item in turn.tool_calls if item.name == "select_skill"), None)
            if not call or call.argument_error:
                return fallback, fallback_confidence, fallback_reason, "keyword_fallback"
            skill_name = str(call.arguments.get("skill_name", ""))
            selected = next((item for item in candidates if item.skill.name == skill_name), None)
            if not selected:
                return fallback, fallback_confidence, fallback_reason, "keyword_fallback"
            confidence = float(call.arguments.get("confidence", 0))
            if not math.isfinite(confidence):
                raise ValueError("Skill 路由置信度必须是有限数字")
            confidence = max(0.0, min(1.0, confidence))
            reason = str(call.arguments.get("reason", "")).strip() or selected.reason
            return selected, confidence, reason, "semantic"
        except Exception:
            return fallback, fallback_confidence, fallback_reason, "keyword_fallback"

    async def _emit_skill_selected(
        self,
        record: RunRecord,
        match: SkillMatch,
        *,
        confidence: float,
        strategy: str,
        reason: str,
        candidates: list[SkillMatch],
    ) -> None:
        await self.events.emit(
            record.run_id,
            "skill_selected",
            record.phase,
            "success",
            match.skill.display_name,
            reason,
            {
                "skill": match.skill.name,
                "description": match.skill.description,
                "score": match.score,
                "confidence": confidence,
                "strategy": strategy,
                "matched_keywords": match.matched_keywords,
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
                "你是产品交互建模模块。先从终端用户视角定义页面流转和核心状态，不编写代码。"
                "必须调用 submit_interaction_model，页面控制在 1–8 个、流转边控制在 1–12 条，"
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
        record.summary = summary
        if plan and status == "completed":
            plan.complete_all()
            await self._emit_plan(record, plan)
        await self.events.emit(
            record.run_id, "run_finished", record.phase, "success" if status == "completed" else "failed",
            "任务完成" if status == "completed" else "任务已停止", summary,
            {
                "status": status,
                "changed_files": record.changed_files,
                "successful_commands": record.successful_commands,
            },
        )

    @staticmethod
    def _initial_messages(
        record: RunRecord,
        match: SkillMatch,
        workspace: Path,
        interaction_model: dict[str, Any] | None = None,
        settings: AgentSettings | None = None,
    ) -> list[dict[str, Any]]:
        settings = settings or AgentSettings()
        interaction_guidance = ""
        if interaction_model:
            interaction_guidance = (
                "\n\n用户已经确认以下产品交互模型。实现必须覆盖其中的页面、流转、状态和验收标准；"
                "完成前要逐项核对，不能擅自改变已确认的核心交互：\n"
                f"{json.dumps(interaction_model, ensure_ascii=False, indent=2)}"
            )
        system = f"""你是 TraceCoder 的决策模块。你不能直接访问文件或终端，只能调用提供的本地工具。

安全与执行规则：
1. 先观察再修改，保持改动聚焦，禁止访问工作区外路径。
2. 工具失败是新的观察：阅读错误、调整参数或换一种方法，不要机械重复。
3. 修改代码后必须运行测试或等价验证。
4. 完成时必须调用 finish；无法继续也要在 summary 中说明事实。
5. 不输出隐藏思维过程。工具调用前只需要选择下一项可验证行动。
6. 工具列表中标注为 Skill 外的操作会暂停并请求用户单次授权；确有必要时应直接调用对应工具，不要用命令绕过权限。
7. 宿主设置了质量关卡：修复类任务修改前要建立基线；修改后要验证；首次 finish 会进入自检，读取改动后才能最终完成。

当前 Skill：{match.skill.display_name}
{match.skill.prompt}{interaction_guidance}

Agent 配置：模式={settings.mode}；强制验证={'是' if settings.require_verification else '否'}；完成前自检={'是' if settings.require_review else '否'}。
工作区：{workspace.name}
最大步骤：由宿主程序控制。"""
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": record.task},
        ]


class PlanTracker:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = deepcopy(items)

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


def _preflight_checkpoint(
    name: str,
    *,
    inspection_observed: bool,
    baseline_observed: bool,
    requires_baseline: bool,
) -> ToolResult | None:
    if name not in {"create_file", "apply_patch"}:
        return None
    if not inspection_observed:
        return _quality_checkpoint(
            "inspect",
            "修改前需要先理解项目",
            "请先使用 list_files、read_file 或 search_text 收集项目证据，再实施修改。",
        )
    if requires_baseline and not baseline_observed:
        return _quality_checkpoint(
            "baseline",
            "修复前需要建立问题基线",
            "请先运行现有测试或检查命令，记录修改前的失败结果。",
        )
    return None


def _quality_checkpoint(
    kind: str,
    summary: str,
    instruction: str,
    *,
    review_path: str = "",
) -> ToolResult:
    data: dict[str, Any] = {
        "checkpoint_required": True,
        "checkpoint_kind": kind,
        "instruction": instruction,
    }
    if review_path:
        data["review_path"] = review_path
    return ToolResult(False, summary, data=data, error=instruction)


def _is_review_action(name: str, arguments: dict[str, Any], changed_files: list[str]) -> bool:
    if name == "read_file":
        return str(arguments.get("path", "")) in changed_files
    if name == "run_command":
        return str(arguments.get("command", "")).strip().startswith("git diff")
    return False


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    safe = deepcopy(arguments)
    for key in list(safe):
        if any(token in key.lower() for token in ("key", "token", "secret", "password")):
            safe[key] = "[REDACTED]"
    for key in ("old_text", "new_text", "content"):
        if isinstance(safe.get(key), str) and len(safe[key]) > 2_000:
            safe[key] = safe[key][:2_000] + "\n... 已截断 ..."
    return safe


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
    for index, item in enumerate(raw_flows, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"flows[{index}] 必须是对象")
        source = clean(item.get("from"), f"flows[{index}].from", 48)
        target = clean(item.get("to"), f"flows[{index}].to", 48)
        if source not in page_ids or target not in page_ids:
            raise ValueError(f"flows[{index}] 引用了不存在的页面 id")
        flows.append({
            "from": source,
            "action": clean(item.get("action"), f"flows[{index}].action", 120),
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
    criteria = [clean(item, f"acceptance_criteria[{index}]", 240) for index, item in enumerate(raw_criteria, start=1)]

    return {
        "title": title,
        "summary": summary,
        "pages": pages,
        "flows": flows,
        "states": states,
        "acceptance_criteria": criteria,
    }


def _compact_context(messages: list[dict[str, Any]], max_chars: int = 48_000) -> list[dict[str, Any]]:
    serialized_length = sum(len(json.dumps(message, ensure_ascii=False)) for message in messages)
    if serialized_length <= max_chars or len(messages) <= 12:
        return messages
    prefix = messages[:2]
    recent = messages[-16:]
    while recent and recent[0].get("role") == "tool":
        recent.pop(0)
    compacted = prefix + [{
        "role": "system",
        "content": f"较早的 {len(messages) - len(prefix) - len(recent)} 条消息已由宿主裁剪；保留的最近工具结果优先。",
    }] + recent
    return compacted
