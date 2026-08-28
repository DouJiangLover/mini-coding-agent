from __future__ import annotations

import asyncio
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from backend.events.store import EventStore
from backend.llm.client import ModelClient
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


class AgentLoop:
    def __init__(
        self,
        events: EventStore,
        skill_router: SkillRouter,
        model: ModelClient,
        max_steps: int | None = None,
    ) -> None:
        self.events = events
        self.skill_router = skill_router
        self.model = model
        self.max_steps = max_steps or int(os.getenv("TRACECODER_MAX_STEPS", "30"))

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
        record.status = "running"
        record.phase = "selecting_skill"
        await self.events.emit(
            record.run_id, "run_started", record.phase, "running", "任务已启动",
            f"工作区：{record.workspace}", {"task": record.task, "workspace": record.workspace, "mode": self.model.mode_name},
        )

        match = self.skill_router.select(record.task)
        await self.events.emit(
            record.run_id, "skill_selected", record.phase, "success", match.skill.display_name,
            match.reason, {"skill": match.skill.name, "description": match.skill.description, "score": match.score},
        )

        record.phase = "planning"
        await self.events.emit(
            record.run_id, "phase_changed", record.phase, "running", "正在制定计划",
            "根据 Skill 策略建立可追踪的执行步骤。",
        )
        plan = PlanTracker(match.skill.plan)
        await self._emit_plan(record, plan)

        registry = ToolRegistry(workspace, match.skill.allowed_tools)
        messages = self._initial_messages(record, match, workspace)
        record.phase = "executing"
        await self.events.emit(
            record.run_id, "phase_changed", record.phase, "running", "进入执行阶段",
            "模型可以通过受控工具观察并修改工作区。",
        )

        recent_fingerprints: list[str] = []
        consecutive_failures = 0
        no_tool_turns = 0
        step_delay = float(os.getenv("TRACECODER_STEP_DELAY", "0.2" if self.model.mode_name == "demo" else "0"))

        for _step in range(1, self.max_steps + 1):
            if record.cancel_requested:
                await self._finish(record, "cancelled", "任务已被用户停止。")
                return

            messages = _compact_context(messages)
            turn = await self.model.complete(messages, registry.schemas())
            messages.append(turn.assistant_message)
            if not turn.tool_calls:
                no_tool_turns += 1
                messages.append({
                    "role": "user",
                    "content": "请继续使用可用工具推进任务；完成时必须调用 finish，不要只输出自然语言。",
                })
                if no_tool_turns >= 3:
                    await self._finish(record, "failed", "模型连续三次未返回工具调用，任务已停止。")
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

                plan.on_tool_started(call.name, bool(record.changed_files))
                if call.name == "finish":
                    next_phase = record.phase
                else:
                    next_phase = "verifying" if call.name == "run_command" and bool(record.changed_files) else "executing"
                if record.phase != next_phase:
                    record.phase = next_phase
                    await self.events.emit(
                        record.run_id, "phase_changed", record.phase, "running",
                        "正在验证修改" if next_phase == "verifying" else "继续执行任务",
                        "运行验证命令检查任务结果。" if next_phase == "verifying" else "根据工具反馈调整下一步行动。",
                    )
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
                else:
                    result = await registry.execute(call.name, call.arguments)

                payload = {"tool": call.name, "arguments": _safe_arguments(call.arguments), **result.data}
                if result.error:
                    payload["error"] = result.error
                await self.events.emit(
                    record.run_id, "tool_finished", record.phase, "success" if result.ok else "failed",
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
                if call.name == "run_command" and result.ok:
                    command = str(result.data.get("command", ""))
                    if command:
                        record.successful_commands.append(command)

                plan.on_tool_finished(call.name, result.ok, bool(record.changed_files))
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
                else:
                    consecutive_failures += 1
                    record.phase = "recovering"
                    await self.events.emit(
                        record.run_id, "error", record.phase, "failed", "工具反馈需要处理",
                        result.error or result.summary, {"tool": call.name, "recoverable": consecutive_failures < 3},
                    )
                    if consecutive_failures >= 3:
                        await self._finish(record, "failed", "连续三次工具执行失败，Agent 已停止以避免无效循环。", plan)
                        return

        await self._finish(record, "failed", f"达到最大执行步数 {self.max_steps}，任务尚未完成。", plan)

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
    def _initial_messages(record: RunRecord, match: SkillMatch, workspace: Path) -> list[dict[str, Any]]:
        system = f"""你是 TraceCoder 的决策模块。你不能直接访问文件或终端，只能调用提供的本地工具。

安全与执行规则：
1. 先观察再修改，保持改动聚焦，禁止访问工作区外路径。
2. 工具失败是新的观察：阅读错误、调整参数或换一种方法，不要机械重复。
3. 修改代码后必须运行测试或等价验证。
4. 完成时必须调用 finish；无法继续也要在 summary 中说明事实。
5. 不输出隐藏思维过程。工具调用前只需要选择下一项可验证行动。

当前 Skill：{match.skill.display_name}
{match.skill.prompt}

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

    def on_tool_started(self, name: str, has_changes: bool) -> None:
        if name in {"list_files", "read_file", "search_text"}:
            self._running("inspect" if not self._is_done("inspect") else "diagnose")
        elif name in {"create_file", "apply_patch"}:
            self._done("inspect")
            self._done("diagnose")
            self._running("edit")
        elif name == "run_command":
            if has_changes:
                self._done("edit")
                self._running("verify")
            else:
                self._done("inspect")
                self._running("diagnose")

    def on_tool_finished(self, name: str, ok: bool, has_changes: bool) -> None:
        if name in {"list_files", "read_file", "search_text"} and ok:
            self._done("inspect")
            self._running("diagnose")
        elif name in {"create_file", "apply_patch"} and ok:
            self._done("edit")
            self._running("verify")
        elif name == "run_command" and ok and has_changes:
            self._done("verify")

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


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    safe = deepcopy(arguments)
    for key in list(safe):
        if any(token in key.lower() for token in ("key", "token", "secret", "password")):
            safe[key] = "[REDACTED]"
    for key in ("old_text", "new_text", "content"):
        if isinstance(safe.get(key), str) and len(safe[key]) > 2_000:
            safe[key] = safe[key][:2_000] + "\n... 已截断 ..."
    return safe


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
