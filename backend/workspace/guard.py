from __future__ import annotations

from pathlib import Path


WORKSPACE_BROWSER_SKIPPED_NAMES = {
    ".git", ".idea", ".next", ".intentflow", ".tracecoder", ".venv", ".vscode",
    "__pycache__", "build", "dist", "node_modules", "venv",
}


class WorkspaceViolation(ValueError):
    pass


class WorkspaceGuard:
    BLOCKED_NAMES = {".env", ".git", ".ssh", ".aws", ".npmrc"}
    BLOCKED_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        if not self.root.is_dir():
            raise WorkspaceViolation(f"工作区不存在或不是目录：{root}")

    def resolve(self, requested: str = ".", *, must_exist: bool = True) -> Path:
        path = Path(requested or ".")
        if path.is_absolute():
            raise WorkspaceViolation("只允许使用工作区内的相对路径")
        candidate = (self.root / path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise WorkspaceViolation("路径越过了工作区边界")
        relative_parts = candidate.relative_to(self.root).parts
        if any(part in self.BLOCKED_NAMES or part.startswith(".env") for part in relative_parts):
            raise WorkspaceViolation("该路径受安全策略保护")
        if candidate.suffix.lower() in self.BLOCKED_SUFFIXES:
            raise WorkspaceViolation("拒绝读取或写入凭据文件")
        if must_exist and not candidate.exists():
            raise WorkspaceViolation(f"路径不存在：{requested}")
        return candidate


def resolve_workspace(project_root: Path, requested: str) -> Path:
    if Path(requested).is_absolute():
        raise WorkspaceViolation("工作区必须是项目根目录下的相对路径")
    root = project_root.resolve()
    candidate = (root / requested).resolve()
    if candidate != root and root not in candidate.parents:
        raise WorkspaceViolation("工作区越过了 INTENTFLOW_WORKSPACE_ROOT")
    if not candidate.is_dir():
        raise WorkspaceViolation(f"工作区不存在：{requested}")
    return candidate


def list_workspace_directories(project_root: Path, requested: str = ".") -> tuple[Path, list[Path]]:
    """Return safe, visible child directories for the local workspace picker."""
    root = project_root.resolve()
    current = resolve_workspace(root, requested)
    directories: list[Path] = []
    try:
        children = sorted(current.iterdir(), key=lambda item: item.name.lower())
    except OSError as exc:
        raise WorkspaceViolation(f"无法读取工作区目录：{requested}") from exc

    for child in children:
        if child.name.startswith(".") or child.name in WORKSPACE_BROWSER_SKIPPED_NAMES:
            continue
        try:
            resolved = child.resolve()
            if not resolved.is_dir():
                continue
            if resolved != root and root not in resolved.parents:
                continue
        except OSError:
            continue
        directories.append(resolved)
        if len(directories) >= 100:
            break
    return current, directories
