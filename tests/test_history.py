import asyncio
from pathlib import Path

from backend.agent.loop import AgentLoop, _compact_context
from backend.events.store import EventStore
from backend.skills.router import SkillRouter
from backend.state import RunRecord, RunStore


SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"


async def _write_finished_run(store: EventStore) -> None:
    store.create("run_history")
    await store.emit(
        "run_history",
        "run_started",
        "selecting_skill",
        "running",
        "任务已启动",
        "工作区：demo",
        {
            "task": "实现一个待办网页",
            "root_task": "实现一个待办网页",
            "workspace": "demo",
            "requested_skill": "auto",
        },
    )
    await store.emit(
        "run_history",
        "run_finished",
        "completed",
        "success",
        "任务完成",
        "页面和测试已完成。",
        {
            "status": "completed",
            "changed_files": ["index.html", "README.md"],
            "successful_commands": ["npm test"],
            "user_guide_path": "README.md",
        },
    )


def test_event_and_run_history_survive_backend_restart(tmp_path: Path) -> None:
    log_root = tmp_path / "runs"
    asyncio.run(_write_finished_run(EventStore(log_root)))

    restored_events = EventStore(log_root)
    channel = restored_events.get("run_history")
    assert channel is not None
    assert channel.terminal
    assert [event["type"] for event in channel.events] == ["run_started", "run_finished"]

    restored_runs = RunStore()
    restored_runs.restore(restored_events.list_channels())
    record = restored_runs.get("run_history")
    assert record is not None
    assert record.status == "completed"
    assert record.root_task == "实现一个待办网页"
    assert record.changed_files == ["index.html", "README.md"]
    assert record.successful_commands == ["npm test"]
    assert record.user_guide_path == "README.md"
    assert restored_runs.latest_for_workspace("demo") is record


async def _write_interrupted_run(store: EventStore) -> None:
    store.create("run_interrupted")
    await store.emit(
        "run_interrupted",
        "run_started",
        "selecting_skill",
        "running",
        "任务已启动",
        "工作区：demo",
        {"task": "修复页面", "workspace": "demo", "requested_skill": "auto"},
    )
    await store.emit(
        "run_interrupted",
        "file_changed",
        "editing",
        "success",
        "文件已修改",
        "补丁已应用",
        {"path": "src/app.js"},
    )
    await store.emit(
        "run_interrupted",
        "tool_finished",
        "verifying",
        "success",
        "运行命令",
        "命令执行成功",
        {"tool": "run_command", "command": "npm test", "exit_code": 0},
    )


def test_interrupted_history_becomes_continuable_failed_run(tmp_path: Path) -> None:
    log_root = tmp_path / "runs"
    asyncio.run(_write_interrupted_run(EventStore(log_root)))

    restored_events = EventStore(log_root)
    channel = restored_events.get("run_interrupted")
    assert channel is not None
    assert channel.terminal
    finished = channel.events[-1]
    assert finished["type"] == "run_finished"
    assert finished["payload"]["interrupted"] is True
    assert finished["payload"]["changed_files"] == ["src/app.js"]
    assert finished["payload"]["successful_commands"] == ["npm test"]

    restored_runs = RunStore()
    restored_runs.restore(restored_events.list_channels())
    record = restored_runs.get("run_interrupted")
    assert record is not None
    assert record.status == "failed"
    assert "继续完成" in record.summary


def test_continuation_messages_keep_goal_and_follow_up_separate(tmp_path: Path) -> None:
    record = RunRecord(
        "run_child",
        "继续完成，先修复上次的测试错误",
        "todo-app",
        root_task="实现一个待办网页",
        parent_run_id="run_parent",
        continuation_context={
            "previous_status": "failed",
            "previous_summary": "测试未通过",
            "changed_files": ["src/app.js"],
            "last_error": "1 test failed",
        },
    )
    router = SkillRouter(SKILLS_ROOT)
    match = router.select(f"{record.root_task}\n{record.task}")

    messages = AgentLoop._initial_messages(record, match, tmp_path / "todo-app")

    assert messages[1]["content"] == "原始任务：实现一个待办网页"
    assert "src/app.js" in messages[2]["content"]
    assert "重新观察当前文件状态" in messages[2]["content"]
    assert messages[3]["content"] == "当前补充要求：继续完成，先修复上次的测试错误"


def test_initial_context_keeps_skill_metadata_but_not_full_prompt(tmp_path: Path) -> None:
    record = RunRecord("run_skill", "修复测试", "demo")
    match = SkillRouter(SKILLS_ROOT).select(record.task)

    messages = AgentLoop._initial_messages(record, match, tmp_path)
    system = str(messages[0]["content"])

    assert match.skill.description in system
    assert match.skill.prompt not in system
    assert "load_skill" in system


def test_context_compaction_keeps_structured_goal_progress_and_files() -> None:
    messages: list[dict[str, object]] = [{"role": "system", "content": "rules"}]
    for index in range(10):
        messages.extend([
            {"role": "assistant", "content": f"step {index}"},
            {
                "role": "tool",
                "name": "read_file",
                "content": '{"ok": true, "data": {"path": "src/app.py", "output": "' + ("x" * 100) + '"}}',
            },
        ])

    compacted, summary = _compact_context(
        messages,
        max_chars=500,
        goal="完成待办应用",
        current_request="继续完成",
        plan_items=[{"id": "inspect", "title": "理解项目", "status": "success"}],
        changed_files=["src/app.py"],
        successful_commands=["pytest -q"],
    )

    assert summary is not None
    assert summary["goal"] == "完成待办应用"
    assert summary["progress"]["done"] == ["理解项目"]
    assert summary["files"]["read"] == ["src/app.py"]
    assert summary["files"]["modified"] == ["src/app.py"]
    assert "完成待办应用" in str(compacted[1]["content"])
