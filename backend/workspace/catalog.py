from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from backend.workspace.guard import WORKSPACE_BROWSER_SKIPPED_NAMES, WorkspaceViolation, resolve_workspace


BUILT_IN_PROJECTS = (
    "calculator",
    "star-catcher",
    "2048-game",
    "approval-demo",
    "order-engine-lab",
)

IGNORED_PROJECT_ENTRIES = {
    ".DS_Store",
    ".pytest_cache",
    "__pycache__",
    "dist",
    "node_modules",
}


class WorkspaceAlreadyExists(WorkspaceViolation):
    pass


def ensure_clean_workspace_root(
    workspace_root: Path,
    templates_root: Path,
    project_names: tuple[str, ...] = BUILT_IN_PROJECTS,
) -> list[str]:
    """Create the local workspace root and seed it once with project templates."""
    root = workspace_root.resolve()
    templates = templates_root.resolve()
    if root.exists():
        if not root.is_dir():
            raise RuntimeError(f"工作区根目录不可用：{root}")
        return []

    root.mkdir(parents=True)

    created: list[str] = []
    for project_name in project_names:
        source = templates / project_name
        target = root / project_name
        if target.exists():
            if not target.is_dir():
                raise RuntimeError(f"项目工作区不是目录：{target}")
            continue
        if not source.is_dir():
            continue
        shutil.copytree(
            source,
            target,
            ignore=shutil.ignore_patterns(*IGNORED_PROJECT_ENTRIES),
        )
        created.append(project_name)
    return created


def move_workspace_to_trash(workspace_root: Path, trash_root: Path, requested: str) -> Path:
    """Move one top-level project workspace to a recoverable local trash directory."""
    root = workspace_root.resolve()
    target = resolve_workspace(root, requested)
    if target == root:
        raise WorkspaceViolation("不能删除工作区根目录")

    relative = target.relative_to(root)
    if len(relative.parts) != 1:
        raise WorkspaceViolation("只能删除完整的顶层项目工作区")

    destination_root = trash_root.resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    destination = destination_root / f"{target.name}-{uuid.uuid4().hex[:10]}"
    try:
        shutil.move(str(target), str(destination))
    except OSError as exc:
        raise WorkspaceViolation(f"无法移入本地回收区：{target.name}") from exc
    return destination


def create_project_workspace(workspace_root: Path, name: str) -> Path:
    """Create one empty top-level project directory under the configured workspace root."""
    root = workspace_root.resolve()
    if not root.is_dir():
        raise WorkspaceViolation(f"工作区根目录不可用：{root}")

    folder_name = name.strip()
    if not 1 <= len(folder_name) <= 80:
        raise WorkspaceViolation("文件夹名称需为 1–80 个字符")
    if folder_name.startswith("."):
        raise WorkspaceViolation("工作区文件夹不能以 . 开头")
    if any(character in folder_name for character in '/\\\0<>:"|?*') or any(ord(character) < 32 for character in folder_name):
        raise WorkspaceViolation("文件夹名称包含不支持的字符")
    if folder_name.casefold() in {value.casefold() for value in WORKSPACE_BROWSER_SKIPPED_NAMES}:
        raise WorkspaceViolation("该文件夹名称由运行环境保留")

    target = root / folder_name
    try:
        target.mkdir()
    except FileExistsError as exc:
        raise WorkspaceAlreadyExists(f"工作区 {folder_name} 已存在") from exc
    except OSError as exc:
        raise WorkspaceViolation(f"无法创建工作区：{folder_name}") from exc
    return target.resolve()
