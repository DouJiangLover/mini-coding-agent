from __future__ import annotations

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


class ModelClient(Protocol):
    mode_name: str

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        ...


class OpenAICompatibleClient:
    mode_name = "model"

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.1,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=20.0)) as client:
            response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
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


class DemoModelClient:
    """Deterministic local planner used only when no API key is configured.

    It still drives the real tool registry, workspace guard, event stream and
    verification loop, which makes the included example reproducible offline.
    """

    mode_name = "demo"

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        history = [message for message in messages if message.get("role") == "tool"]
        if not history:
            return self._call("list_files", {"path": ".", "max_depth": 3})

        last = history[-1]
        name = last.get("name", "")
        try:
            result = json.loads(last.get("content") or "{}")
        except json.JSONDecodeError:
            result = {}

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
    api_key = os.getenv("LLM_API_KEY", "").strip()
    demo_setting = os.getenv("TRACECODER_DEMO", "auto").strip().lower()
    use_demo = demo_setting in {"1", "true", "yes"} or (demo_setting == "auto" and not api_key)
    if use_demo:
        return DemoModelClient()
    if not api_key:
        raise RuntimeError("模型模式需要设置 LLM_API_KEY；或将 TRACECODER_DEMO 设为 true")
    return OpenAICompatibleClient(
        api_key=api_key,
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
        model=os.getenv("LLM_MODEL", "gpt-4.1-mini"),
    )
