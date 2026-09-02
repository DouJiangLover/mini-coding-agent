from pathlib import Path

import pytest

from backend.workspace.catalog import (
    WorkspaceAlreadyExists,
    create_project_workspace,
    ensure_clean_workspace_root,
    move_workspace_to_trash,
)
from backend.workspace.guard import (
    WorkspaceGuard,
    WorkspaceViolation,
    list_workspace_directories,
    resolve_workspace,
)


def test_guard_accepts_file_inside_workspace(tmp_path: Path):
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir()
    source.write_text("print('ok')\n", encoding="utf-8")

    guard = WorkspaceGuard(tmp_path)

    assert guard.resolve("src/main.py") == source.resolve()


def test_guard_rejects_path_traversal(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    guard = WorkspaceGuard(workspace)

    with pytest.raises(WorkspaceViolation, match="边界"):
        guard.resolve("../secret.txt", must_exist=False)


def test_guard_rejects_credentials(tmp_path: Path):
    secret = tmp_path / ".env"
    secret.write_text("KEY=secret", encoding="utf-8")

    with pytest.raises(WorkspaceViolation, match="安全策略"):
        WorkspaceGuard(tmp_path).resolve(".env")


def test_workspace_must_stay_under_configured_root(tmp_path: Path):
    (tmp_path / "project").mkdir()

    with pytest.raises(WorkspaceViolation, match="越过"):
        resolve_workspace(tmp_path / "project", "../")


def test_workspace_browser_lists_only_safe_child_directories(tmp_path: Path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "Beta").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "plain.txt").write_text("not a directory", encoding="utf-8")

    current, directories = list_workspace_directories(tmp_path)

    assert current == tmp_path.resolve()
    assert [directory.name for directory in directories] == ["alpha", "Beta"]


def test_workspace_browser_rejects_outside_directory(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(WorkspaceViolation, match="越过"):
        list_workspace_directories(root, "../")


def test_clean_workspace_root_contains_only_project_copies(tmp_path: Path):
    templates = tmp_path / "templates"
    (templates / "calculator" / "src").mkdir(parents=True)
    (templates / "calculator" / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (templates / "calculator" / ".pytest_cache").mkdir()
    (templates / "calculator" / ".pytest_cache" / "cache").write_text("ignored", encoding="utf-8")
    workspace_root = tmp_path / "workspaces"

    created = ensure_clean_workspace_root(workspace_root, templates, ("calculator",))

    assert created == ["calculator"]
    assert [entry.name for entry in workspace_root.iterdir()] == ["calculator"]
    assert (workspace_root / "calculator" / "src" / "main.py").is_file()
    assert not (workspace_root / "calculator" / ".pytest_cache").exists()

    (workspace_root / "calculator" / "src" / "main.py").write_text("user change\n", encoding="utf-8")
    assert ensure_clean_workspace_root(workspace_root, templates, ("calculator",)) == []
    assert (workspace_root / "calculator" / "src" / "main.py").read_text(encoding="utf-8") == "user change\n"

    move_workspace_to_trash(workspace_root, tmp_path / "trash", "calculator")
    assert ensure_clean_workspace_root(workspace_root, templates, ("calculator",)) == []
    assert not (workspace_root / "calculator").exists()


def test_move_top_level_workspace_to_recoverable_trash(tmp_path: Path):
    workspace_root = tmp_path / "workspaces"
    project = workspace_root / "calculator"
    project.mkdir(parents=True)
    (project / "main.py").write_text("print('ok')\n", encoding="utf-8")

    destination = move_workspace_to_trash(workspace_root, tmp_path / "trash", "calculator")

    assert not project.exists()
    assert destination.parent == (tmp_path / "trash").resolve()
    assert destination.name.startswith("calculator-")
    assert (destination / "main.py").read_text(encoding="utf-8") == "print('ok')\n"


def test_workspace_deletion_rejects_root_and_nested_directories(tmp_path: Path):
    workspace_root = tmp_path / "workspaces"
    (workspace_root / "calculator" / "src").mkdir(parents=True)

    with pytest.raises(WorkspaceViolation, match="根目录"):
        move_workspace_to_trash(workspace_root, tmp_path / "trash", ".")

    with pytest.raises(WorkspaceViolation, match="顶层项目"):
        move_workspace_to_trash(workspace_root, tmp_path / "trash", "calculator/src")


def test_create_empty_top_level_project_workspace(tmp_path: Path):
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    created = create_project_workspace(workspace_root, "新项目")

    assert created == (workspace_root / "新项目").resolve()
    assert created.is_dir()
    assert list(created.iterdir()) == []


@pytest.mark.parametrize("name", ["../escape", ".hidden", "nested/project", "node_modules", "bad:name"])
def test_create_workspace_rejects_unsafe_or_reserved_names(tmp_path: Path, name: str):
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()

    with pytest.raises(WorkspaceViolation):
        create_project_workspace(workspace_root, name)


def test_create_workspace_rejects_existing_directory(tmp_path: Path):
    workspace_root = tmp_path / "workspaces"
    (workspace_root / "existing").mkdir(parents=True)

    with pytest.raises(WorkspaceAlreadyExists, match="已存在"):
        create_project_workspace(workspace_root, "existing")
