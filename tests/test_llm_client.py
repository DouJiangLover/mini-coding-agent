import asyncio
import json

import httpx

from backend.llm.client import DemoModelClient, ModelRequestError, OpenAICompatibleClient, create_model_client


def test_defaults_to_demo_without_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("INTENTFLOW_DEMO", "auto")

    client = create_model_client()

    assert isinstance(client, DemoModelClient)


def test_deepseek_key_uses_current_default_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-key")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("INTENTFLOW_DEMO", "false")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    client = create_model_client()

    assert isinstance(client, OpenAICompatibleClient)
    assert client.provider_name == "deepseek"
    assert client.base_url == "https://api.deepseek.com"
    assert client.model_name == "deepseek-v4-flash"


def test_deepseek_request_disables_thinking(monkeypatch):
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "done"}}],
        })

    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        return original_client(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout"))

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)
    client = OpenAICompatibleClient(
        api_key="test-only-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        provider="deepseek",
    )

    asyncio.run(client.complete([{"role": "user", "content": "hello"}], []))

    assert captured["thinking"] == {"type": "disabled"}


def test_internal_decision_tool_is_required(monkeypatch):
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "route_1",
                    "type": "function",
                    "function": {
                        "name": "select_skills",
                        "arguments": json.dumps({
                            "skill_names": ["frontend_build"],
                            "confidence": 0.9,
                            "reason": "创建网页产品",
                        }),
                    },
                }],
            }}],
        })

    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        return original_client(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout"))

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)
    client = OpenAICompatibleClient(
        api_key="test-only-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        provider="deepseek",
    )
    route_tool = [{
        "type": "function",
        "function": {
            "name": "select_skills",
            "description": "select",
            "parameters": {"type": "object", "properties": {}},
        },
    }]

    asyncio.run(client.complete([{"role": "user", "content": "build a website"}], route_tool))

    assert captured["tool_choice"] == {
        "type": "function",
        "function": {"name": "select_skills"},
    }


def test_transient_connection_failure_is_retried(monkeypatch):
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("temporary connection failure", request=request)
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "done"}}],
        })

    async def no_wait(_delay: float) -> None:
        return None

    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        return original_client(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout"))

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)
    monkeypatch.setattr("backend.llm.client.asyncio.sleep", no_wait)
    client = OpenAICompatibleClient(
        api_key="test-only-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        provider="deepseek",
    )

    result = asyncio.run(client.complete([{"role": "user", "content": "hello"}], []))

    assert result.content == "done"
    assert attempts == 3


def test_connection_failure_has_actionable_message(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("", request=request)

    async def no_wait(_delay: float) -> None:
        return None

    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        return original_client(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout"))

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)
    monkeypatch.setattr("backend.llm.client.asyncio.sleep", no_wait)
    client = OpenAICompatibleClient(
        api_key="test-only-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        provider="deepseek",
    )

    try:
        asyncio.run(client.complete([{"role": "user", "content": "hello"}], []))
    except ModelRequestError as exc:
        assert str(exc) == "连续 3 次无法连接模型服务，请检查网络或稍后重试。"
    else:
        raise AssertionError("expected ModelRequestError")


def test_demo_mode_explains_that_confirmed_product_builds_need_a_real_model():
    client = DemoModelClient()
    messages = [
        {
            "role": "system",
            "content": "用户已经确认以下产品交互模型：2048 小游戏",
        },
        {
            "role": "user",
            "content": "请按照需求文档从头实现这个 2048 网页游戏。",
        },
    ]

    try:
        asyncio.run(client.complete(messages, []))
    except ModelRequestError as exc:
        assert "本地演示模式只提供 calculator 和 star-catcher" in str(exc)
        assert "请配置 DeepSeek" in str(exc)
    else:
        raise AssertionError("expected ModelRequestError")
