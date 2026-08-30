from pathlib import Path

import pytest

from backend.settings import AgentSettingsStore


def test_agent_settings_persist_and_reload(tmp_path: Path) -> None:
    path = tmp_path / "agent-settings.json"
    store = AgentSettingsStore(path)

    updated = store.update({
        "mode": "safe",
        "max_steps": 42,
        "failure_limit": 5,
        "interaction_first": False,
        "require_verification": True,
        "require_review": False,
        "context_budget": 64_000,
        "command_timeout": 20,
    })

    assert updated.mode == "safe"
    assert updated.max_steps == 42
    restored = AgentSettingsStore(path).get()
    assert restored == updated
    assert restored.interaction_first is False
    assert restored.command_timeout == 20


def test_agent_settings_reject_invalid_values(tmp_path: Path) -> None:
    store = AgentSettingsStore(tmp_path / "agent-settings.json")

    with pytest.raises(ValueError, match="max_steps"):
        store.update({"max_steps": 101})

    with pytest.raises(ValueError, match="运行模式"):
        store.update({"mode": "unsafe"})


def test_agent_settings_reset_to_defaults(tmp_path: Path) -> None:
    store = AgentSettingsStore(tmp_path / "agent-settings.json")
    store.update({"mode": "read_only", "max_steps": 12})

    reset = store.reset()

    assert reset.mode == "standard"
    assert reset.max_steps == 30
    assert reset.require_verification is True
