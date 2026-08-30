from backend.state import RunRecord, RunStore


def test_run_store_detects_active_workspace() -> None:
    store = RunStore()
    active = RunRecord("run_active", "build project", "examples/2048-game")
    finished = RunRecord("run_done", "fix tests", "examples/calculator", status="completed")
    store.add(active)
    store.add(finished)

    assert store.has_active_workspace("examples/2048-game")
    assert store.has_active_workspace("examples/2048-game/src")
    assert store.has_active_workspace("examples")
    assert not store.has_active_workspace("examples/calculator")

    active.status = "failed"
    assert not store.has_active_workspace("examples/2048-game")


def test_run_store_lists_recent_tasks_and_keeps_attention_tasks_active() -> None:
    store = RunStore()
    older = RunRecord("run_old", "first task", "examples/calculator")
    older.created_at = "2026-08-28T10:00:00+08:00"
    newer = RunRecord("run_new", "second task", "examples/2048-game")
    newer.created_at = "2026-08-29T10:00:00+08:00"
    newer.status = "waiting_skill_confirmation"
    store.add(older)
    store.add(newer)

    assert [run.run_id for run in store.list_recent()] == ["run_new", "run_old"]
    assert store.has_active_workspace("examples/2048-game")
