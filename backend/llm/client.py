from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]
    argument_error: str | None = None


@dataclass
class ModelTurn:
    assistant_message: dict[str, Any]
    tool_calls: list[ToolCall] = field(default_factory=list)
    content: str = ""


class ModelRequestError(RuntimeError):
    """A user-facing model request failure with credentials kept out of logs."""


class ModelClient(Protocol):
    mode_name: str
    provider_name: str
    model_name: str

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        ...


class OpenAICompatibleClient:
    mode_name = "model"
    request_attempts = 3

    def __init__(self, api_key: str, base_url: str, model: str, provider: str = "openai-compatible") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.provider_name = provider
        self.model_name = model

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.1,
        }
        if len(tools) == 1:
            required_tool = str(tools[0].get("function", {}).get("name", ""))
            if required_tool in {"select_skill", "select_skills", "submit_interaction_model"}:
                payload["tool_choice"] = {
                    "type": "function",
                    "function": {"name": required_tool},
                }
        if self.provider_name == "deepseek":
            # DeepSeek V4 enables thinking by default. Non-thinking mode keeps
            # the tool loop OpenAI-compatible without reasoning_content state.
            payload["thinking"] = {"type": "disabled"}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        response = await self._post_with_retries(headers, payload)
        body = response.json()
        message = body["choices"][0]["message"]
        calls: list[ToolCall] = []
        for raw_call in message.get("tool_calls") or []:
            raw_arguments = raw_call.get("function", {}).get("arguments", "{}")
            try:
                arguments = json.loads(raw_arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("工具参数必须是 JSON 对象")
                argument_error = None
            except (json.JSONDecodeError, ValueError) as exc:
                arguments = {}
                argument_error = f"工具参数 JSON 无效：{exc}"
            calls.append(ToolCall(
                call_id=raw_call.get("id") or f"call_{uuid.uuid4().hex[:10]}",
                name=raw_call.get("function", {}).get("name", ""),
                arguments=arguments,
                argument_error=argument_error,
            ))
        assistant = {
            "role": "assistant",
            "content": message.get("content"),
        }
        if message.get("tool_calls"):
            assistant["tool_calls"] = message["tool_calls"]
        return ModelTurn(assistant_message=assistant, tool_calls=calls, content=message.get("content") or "")

    async def _post_with_retries(
        self,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> httpx.Response:
        endpoint = f"{self.base_url}/chat/completions"
        for attempt in range(1, self.request_attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=20.0)) as client:
                    response = await client.post(endpoint, headers=headers, json=payload)
                    response.raise_for_status()
                    return response
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                retryable = status == 429 or status >= 500
                if retryable and attempt < self.request_attempts:
                    await asyncio.sleep(self._retry_delay(attempt))
                    continue
                raise ModelRequestError(self._http_error_message(status)) from exc
            except httpx.TimeoutException as exc:
                if attempt < self.request_attempts:
                    await asyncio.sleep(self._retry_delay(attempt))
                    continue
                raise ModelRequestError(
                    f"模型服务连续 {self.request_attempts} 次响应超时，请检查网络后重试。"
                ) from exc
            except httpx.RequestError as exc:
                if attempt < self.request_attempts:
                    await asyncio.sleep(self._retry_delay(attempt))
                    continue
                raise ModelRequestError(
                    f"连续 {self.request_attempts} 次无法连接模型服务，请检查网络或稍后重试。"
                ) from exc

        raise ModelRequestError("模型请求未能完成，请稍后重试。")

    @staticmethod
    def _retry_delay(attempt: int) -> float:
        return 0.6 * (2 ** (attempt - 1))

    @staticmethod
    def _http_error_message(status: int) -> str:
        if status in {401, 403}:
            return "模型服务拒绝认证，请检查 DeepSeek API Key 是否有效且已正确加载。"
        if status == 402:
            return "模型账户余额不足，请充值后重新运行任务。"
        if status == 429:
            return "模型服务当前请求过多，自动重试后仍未恢复，请稍后再试。"
        if status >= 500:
            return f"模型服务暂时不可用（HTTP {status}），自动重试后仍未恢复。"
        return f"模型服务拒绝了请求（HTTP {status}），请检查模型与接口配置。"


class DemoModelClient:
    """Deterministic local planner used only when no API key is configured.

    It still drives the real tool registry, workspace guard, event stream and
    verification loop, which makes the included example reproducible offline.
    """

    mode_name = "demo"
    provider_name = "local-demo"
    model_name = "deterministic-demo"

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        history = [message for message in messages if message.get("role") == "tool"]
        task_text = "\n".join(
            str(message.get("content") or "")
            for message in messages
            if message.get("role") == "user"
        ).lower()
        system_text = "\n".join(
            str(message.get("content") or "")
            for message in messages
            if message.get("role") == "system"
        )
        tool_names = {
            str(tool.get("function", {}).get("name", ""))
            for tool in tools
            if isinstance(tool, dict)
        }
        if "submit_interaction_model" in tool_names:
            return self._interaction_model(task_text)
        if "用户已经确认以下产品交互模型" in system_text:
            raise ModelRequestError(
                "本地演示模式只提供 calculator 和 star-catcher 的确定性修复闭环；"
                "从需求文档构建新产品请配置 DeepSeek 或其他 OpenAI-compatible 模型。"
            )
        if "load_skill" in tool_names and not any(message.get("name") == "load_skill" for message in history):
            load_schema = next(
                tool for tool in tools
                if tool.get("function", {}).get("name") == "load_skill"
            )
            candidates = (
                load_schema.get("function", {})
                .get("parameters", {})
                .get("properties", {})
                .get("skill_name", {})
                .get("enum", [])
            )
            if candidates:
                return self._call("load_skill", {"skill_name": str(candidates[0])})
        actionable_history = [message for message in history if message.get("name") != "load_skill"]
        history_text = "\n".join(str(message.get("content") or "") for message in history).lower()
        is_star_workspace = all(marker in history_text for marker in ("package.json", "game.js", "game.test.js"))
        if "星星捕手" in task_text or "star-catcher" in task_text or "combo" in task_text or is_star_workspace:
            return self._complete_star_catcher(actionable_history)

        if not actionable_history:
            return self._call("list_files", {"path": ".", "max_depth": 3})

        last = actionable_history[-1]
        name = last.get("name", "")
        try:
            result = json.loads(last.get("content") or "{}")
        except json.JSONDecodeError:
            result = {}

        if name == "finish" and result.get("data", {}).get("checkpoint_kind") == "review":
            return self._call("read_file", {
                "path": result.get("data", {}).get("review_path", "src/calculator.py"),
                "start_line": 1,
                "end_line": 200,
            })
        if name == "read_file" and self._review_checkpoint_pending(history):
            return self._call("finish", {
                "summary": "已完成修复、验证和完成前自检。",
                "verification": "测试通过，并重新读取改动文件确认实现",
            })

        if name == "list_files":
            return self._call("run_command", {"command": "pytest -q", "timeout": 30})
        if name == "run_command":
            if result.get("ok"):
                return self._call("finish", {
                    "summary": "已完成修复，示例项目的全部测试均已通过。",
                    "verification": "pytest -q 执行成功",
                })
            return self._call("read_file", {"path": "src/calculator.py", "start_line": 1, "end_line": 120})
        if name == "read_file" and result.get("data", {}).get("path") == "src/calculator.py":
            return self._call("read_file", {"path": "tests/test_calculator.py", "start_line": 1, "end_line": 160})
        if name == "read_file":
            return self._call("apply_patch", {
                "path": "src/calculator.py",
                "old_text": "    return a - b  # BUG: addition should use +\n",
                "new_text": "    return a + b\n",
            })
        if name == "apply_patch":
            if not result.get("ok"):
                return self._call("read_file", {"path": "src/calculator.py", "start_line": 1, "end_line": 120})
            return self._call("run_command", {"command": "pytest -q", "timeout": 30})
        return self._call("finish", {
            "summary": "演示流程已结束，请检查最后一条工具结果。",
            "verification": "已执行本地工具链",
        })

    def _interaction_model(self, task_text: str) -> ModelTurn:
        if "2048" in task_text:
            return self._call("submit_interaction_model", {
                "title": "2048 小游戏",
                "summary": "玩家通过方向操作合并数字、累计分数，并在胜利或无路可走时获得明确反馈。",
                "pages": [
                    {"id": "game", "name": "游戏主界面", "purpose": "展示棋盘、分数、最高分和主要操作"},
                    {"id": "result", "name": "结果提示层", "purpose": "展示胜利或失败状态并提供后续选择"},
                ],
                "flows": [
                    {"from": "game", "action": "点击新游戏", "to": "game"},
                    {"from": "game", "action": "合成 2048 或无可用移动", "to": "result"},
                    {"from": "result", "action": "继续游戏或重新开始", "to": "game"},
                ],
                "states": [
                    {"from": "ready", "event": "开始新游戏", "to": "playing"},
                    {"from": "playing", "event": "合成 2048", "to": "won"},
                    {"from": "won", "event": "选择继续", "to": "playing"},
                    {"from": "playing", "event": "无可用移动", "to": "lost"},
                    {"from": "lost", "event": "开始新游戏", "to": "playing"},
                ],
                "acceptance_criteria": [
                    "键盘和触控都能完成四方向移动",
                    "胜利后可以继续游戏，失败后可以重新开始",
                    "分数与历史最高分正确显示并保存",
                    "移动端棋盘不横向溢出，自动化测试通过",
                ],
            })
        return self._call("submit_interaction_model", {
            "title": "目标 Web 应用",
            "summary": "用户从主页面进入核心功能，完成操作后获得清晰结果反馈。",
            "pages": [
                {"id": "home", "name": "主页面", "purpose": "展示产品入口与核心信息"},
                {"id": "result", "name": "结果区域", "purpose": "反馈用户操作结果"},
            ],
            "flows": [
                {"from": "home", "action": "执行核心操作", "to": "result"},
                {"from": "result", "action": "返回或再次操作", "to": "home"},
            ],
            "states": [
                {"from": "idle", "event": "用户开始操作", "to": "active"},
                {"from": "active", "event": "操作完成", "to": "completed"},
            ],
            "acceptance_criteria": ["核心流程可以完整走通", "操作结果有清晰反馈", "页面在桌面端和移动端均可使用"],
        })

    def _complete_star_catcher(self, history: list[dict[str, Any]]) -> ModelTurn:
        if not history:
            return self._call("list_files", {"path": ".", "max_depth": 3})

        last = history[-1]
        name = last.get("name", "")
        try:
            result = json.loads(last.get("content") or "{}")
        except json.JSONDecodeError:
            result = {}

        if name == "finish" and result.get("data", {}).get("checkpoint_kind") == "review":
            return self._call("read_file", {
                "path": result.get("data", {}).get("review_path", "src/game.js"),
                "start_line": 1,
                "end_line": 200,
            })
        if name == "read_file" and self._review_checkpoint_pending(history):
            return self._call("finish", {
                "summary": "已修复星星捕手的连击计分错误，并完成改动自检。",
                "verification": "npm test 通过，并重新读取改动文件确认实现",
            })

        if name == "list_files":
            return self._call("run_command", {"command": "npm test", "timeout": 30})
        if name == "run_command":
            if result.get("ok"):
                return self._call("finish", {
                    "summary": "已修复星星捕手的连击计分错误，全部 JavaScript 测试通过。",
                    "verification": "npm test 执行成功",
                })
            return self._call("read_file", {"path": "src/game.js", "start_line": 1, "end_line": 160})
        if name == "read_file" and result.get("data", {}).get("path") == "src/game.js":
            return self._call("read_file", {"path": "test/game.test.js", "start_line": 1, "end_line": 180})
        if name == "read_file":
            return self._call("apply_patch", {
                "path": "src/game.js",
                "old_text": (
                    "    score: state.score + gainedScore,\n"
                    "    combo: 0,\n"
                    "    bestCombo: Math.max(state.bestCombo, nextCombo),\n"
                ),
                "new_text": (
                    "    score: state.score + gainedScore,\n"
                    "    combo: nextCombo,\n"
                    "    bestCombo: Math.max(state.bestCombo, nextCombo),\n"
                ),
            })
        if name == "apply_patch":
            if not result.get("ok"):
                return self._call("read_file", {"path": "src/game.js", "start_line": 1, "end_line": 160})
            return self._call("run_command", {"command": "npm test", "timeout": 30})
        return self._call("finish", {
            "summary": "网页游戏演示流程已结束，请检查最后一条工具结果。",
            "verification": "已执行本地工具链",
        })

    @staticmethod
    def _review_checkpoint_pending(history: list[dict[str, Any]]) -> bool:
        for message in reversed(history[:-1]):
            if message.get("name") != "finish":
                continue
            try:
                result = json.loads(message.get("content") or "{}")
            except json.JSONDecodeError:
                return False
            return result.get("data", {}).get("checkpoint_kind") == "review"
        return False

    @staticmethod
    def _call(name: str, arguments: dict[str, Any]) -> ModelTurn:
        call_id = f"demo_{uuid.uuid4().hex[:10]}"
        raw_call = {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
        }
        return ModelTurn(
            assistant_message={"role": "assistant", "content": None, "tool_calls": [raw_call]},
            tool_calls=[ToolCall(call_id=call_id, name=name, arguments=arguments)],
        )


def create_model_client() -> ModelClient:
    deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    generic_api_key = os.getenv("LLM_API_KEY", "").strip()
    api_key = deepseek_api_key or generic_api_key
    demo_setting = (
        os.getenv("INTENTFLOW_DEMO")
        or os.getenv("TRACECODER_DEMO")
        or "auto"
    ).strip().lower()
    use_demo = demo_setting in {"1", "true", "yes"} or (demo_setting == "auto" and not api_key)
    if use_demo:
        return DemoModelClient()
    if not api_key:
        raise RuntimeError("模型模式需要设置 DEEPSEEK_API_KEY（或 LLM_API_KEY）；也可将 INTENTFLOW_DEMO 设为 true")

    base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").strip()
    model = os.getenv("LLM_MODEL", "deepseek-v4-flash").strip()
    provider = "deepseek" if deepseek_api_key or "api.deepseek.com" in base_url.lower() else "openai-compatible"
    return OpenAICompatibleClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider=provider,
    )
