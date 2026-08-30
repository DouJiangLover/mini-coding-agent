from pathlib import Path

import pytest

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
