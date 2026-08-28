from backend.state import RunRecord, RunStore


def test_run_store_detects_active_workspace() -> None:
    store = RunStore()
    active = RunRecord("run_active", "build project", "examples/2048-game")
    finished = RunRecord("run_done", "fix tests", "examples/calculator", status="completed")
    store.add(active)
    store.add(finished)

    assert store.has_active_workspace("examples/2048-game")
    assert not store.has_active_workspace("examples/calculator")

    active.status = "failed"
    assert not store.has_active_workspace("examples/2048-game")
