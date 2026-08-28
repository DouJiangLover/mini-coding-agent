import asyncio
import shutil
from pathlib import Path

from backend.agent.loop import AgentLoop
from backend.events.store import EventStore
from backend.llm.client import DemoModelClient
from backend.skills.router import SkillRouter
from backend.state import RunRecord


SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"
EXAMPLES_ROOT = Path(__file__).resolve().parents[1] / "examples"


def test_demo_agent_repairs_and_verifies_project(tmp_path: Path):
    workspace = tmp_path / "calculator"
    (workspace / "src").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / "src" / "__init__.py").write_text("", encoding="utf-8")
    (workspace / "src" / "calculator.py").write_text(
        "def add(a, b):\n    return a - b  # BUG: addition should use +\n",
        encoding="utf-8",
    )
    (workspace / "tests" / "test_calculator.py").write_text(
        "from src.calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    event_store = EventStore(tmp_path / "events")
    event_store.create("run_test")
    record = RunRecord("run_test", "修复失败测试", "calculator")
    loop = AgentLoop(event_store, SkillRouter(SKILLS_ROOT), DemoModelClient(), max_steps=10)

    asyncio.run(loop.run(record, workspace))

    assert record.status == "completed"
    assert "return a + b" in (workspace / "src" / "calculator.py").read_text(encoding="utf-8")
    assert record.successful_commands == ["pytest -q"]
    assert event_store.get("run_test").terminal
    plan_events = [event for event in event_store.get("run_test").events if event["type"] == "plan_updated"]
    assert plan_events[0]["payload"]["items"][0]["status"] == "running"
    assert plan_events[-1]["payload"]["items"][-1]["status"] == "success"


def test_demo_agent_repairs_star_catcher_combo(tmp_path: Path):
    workspace = tmp_path / "star-catcher"
    shutil.copytree(EXAMPLES_ROOT / "star-catcher", workspace)
    event_store = EventStore(tmp_path / "events")
    event_store.create("run_game")
    record = RunRecord("run_game", "修复当前工作区中失败的测试", "star-catcher")
    loop = AgentLoop(event_store, SkillRouter(SKILLS_ROOT), DemoModelClient(), max_steps=10)

    asyncio.run(loop.run(record, workspace))

    assert record.status == "completed"
    assert "combo: nextCombo" in (workspace / "src" / "game.js").read_text(encoding="utf-8")
    assert record.successful_commands == ["npm test"]
    assert event_store.get("run_game").terminal
