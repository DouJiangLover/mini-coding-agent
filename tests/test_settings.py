from pathlib import Path

import pytest

from backend.agent.loop import AgentLoop
from backend.events.store import EventStore
from backend.llm.client import DemoModelClient
from backend.settings import AgentSettingsStore
from backend.skills.router import SkillRouter


SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"


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
    assert reset.max_steps == 45
    assert reset.require_verification is True


def test_saved_step_budget_overrides_environment_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTENTFLOW_MAX_STEPS", "30")
    store = AgentSettingsStore(tmp_path / "agent-settings.json")
    loop = AgentLoop(
        EventStore(tmp_path / "events"),
        SkillRouter(SKILLS_ROOT),
        DemoModelClient(),
        settings_store=store,
    )

    assert loop._resolve_max_steps(store.get()) == (30, "environment_default")

    saved = store.update({"max_steps": 60})

    assert loop._resolve_max_steps(saved) == (60, "saved_settings")
