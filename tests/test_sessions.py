from pathlib import Path

from backend.sessions.store import SessionStore


def test_session_tree_persists_branches_and_active_paths(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    store = SessionStore(root)
    session = store.create("todo-app", "实现待办应用")
    first = store.create_entry(
        session.session_id,
        run_id="run_root",
        task="实现待办应用",
        root_task="实现待办应用",
    )
    main_child = store.create_entry(
        session.session_id,
        run_id="run_main",
        task="继续完成",
        root_task="实现待办应用",
        parent_id=first.entry_id,
    )
    branch_child = store.create_entry(
        session.session_id,
        run_id="run_branch",
        task="改用本地存储方案",
        root_task="实现待办应用",
        parent_id=first.entry_id,
    )
    store.update_entry(
        branch_child.entry_id,
        status="completed",
        summary="分支完成",
        changed_files=["src/storage.ts"],
        successful_commands=["npm test"],
        structured_summary={"goal": "实现待办应用", "progress": {"done": ["本地存储"]}},
    )

    restored = SessionStore(root)
    tree = restored.public_tree(session.session_id, branch_child.entry_id)
    path = restored.path(session.session_id, branch_child.entry_id)

    assert tree is not None
    assert len(tree["entries"]) == 3
    assert [entry.run_id for entry in path] == ["run_root", "run_branch"]
    assert main_child.entry_id != branch_child.entry_id
    assert restored.latest_summary(session.session_id, branch_child.entry_id)["goal"] == "实现待办应用"
