import asyncio
import json

import httpx

from backend.llm.client import DemoModelClient, OpenAICompatibleClient, create_model_client


def test_defaults_to_demo_without_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("TRACECODER_DEMO", "auto")

    client = create_model_client()

    assert isinstance(client, DemoModelClient)


def test_deepseek_key_uses_current_default_model(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-key")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("TRACECODER_DEMO", "false")
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
