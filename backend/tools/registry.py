from __future__ import annotations

import asyncio
import difflib
import os
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from backend.workspace.guard import WorkspaceGuard, WorkspaceViolation


MAX_TEXT_CHARS = 12_000
MAX_CREATED_FILE_CHARS = 80_000
SKIPPED_DIRS = {".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__", ".next"}
TEXT_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".md", ".txt", ".css", ".html",
    ".toml", ".yaml", ".yml", ".ini", ".cfg", ".java", ".go", ".rs", ".c", ".h", ".cpp",
}


@dataclass
class ToolResult:
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    truncated: bool = False
    approval_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "data": self.data,
            "error": self.error,
            "truncated": self.truncated,
            "approval_required": self.approval_required,
        }


class ToolApprovalRequired(ValueError):
    def __init__(self, reason: str, *, risk: str = "medium") -> None:
        super().__init__(reason)
        self.reason = reason
        self.risk = risk


class ToolRegistry:
    def __init__(
        self,
        workspace: Path,
        allowed_tools: list[str] | None = None,
        *,
        agent_mode: str = "standard",
        max_command_timeout: int = 60,
    ) -> None:
        self.guard = WorkspaceGuard(workspace)
        self.allowed_tools = set(allowed_tools or self.available_names())
        self.agent_mode = agent_mode
        self.max_command_timeout = max(1, min(int(max_command_timeout), 60))
        self.forced_approval_tools = {"create_file", "apply_patch", "run_command"} if agent_mode == "safe" else set()
        self.blocked_tools = {"create_file", "apply_patch"} if agent_mode == "read_only" else set()
        if agent_mode == "autonomous":
            self.allowed_tools = set(self.available_names())
        self._handlers: dict[str, Callable[..., Any]] = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "search_text": self.search_text,
            "create_file": self.create_file,
            "apply_patch": self.apply_patch,
            "run_command": self.run_command,
            "finish": self.finish,
        }

    @staticmethod
    def available_names() -> list[str]:
        return ["list_files", "read_file", "search_text", "create_file", "apply_patch", "run_command", "finish"]

    def schemas(self) -> list[dict[str, Any]]:
        schemas = [
            _schema("list_files", "列出工作区内的目录树。", {
                "path": {"type": "string", "description": "相对工作区的目录，默认 ."},
                "max_depth": {"type": "integer", "description": "递归深度，1 到 4，默认 3"},
            }),
            _schema("read_file", "按行读取 UTF-8 文本文件。", {
                "path": {"type": "string", "description": "相对文件路径"},
                "start_line": {"type": "integer", "description": "起始行，默认 1"},
                "end_line": {"type": "integer", "description": "结束行，最多读取 400 行"},
            }, required=["path"]),
            _schema("search_text", "在工作区文本文件中搜索字符串。", {
                "query": {"type": "string", "description": "要搜索的文本"},
                "path": {"type": "string", "description": "搜索目录，默认 ."},
            }, required=["query"]),
            _schema("create_file", "在工作区内创建一个新的 UTF-8 文本文件；文件已存在时拒绝覆盖。", {
                "path": {"type": "string", "description": "新文件的相对路径，可自动创建父目录"},
                "content": {"type": "string", "description": "完整文件内容，最多 80000 个字符"},
            }, required=["path", "content"]),
            _schema("apply_patch", "把文件中唯一匹配的 old_text 替换为 new_text，并返回 diff。", {
                "path": {"type": "string", "description": "相对文件路径"},
                "old_text": {"type": "string", "description": "文件中必须唯一出现的原文本"},
                "new_text": {"type": "string", "description": "替换后的文本"},
            }, required=["path", "old_text", "new_text"]),
            _schema("run_command", "在工作区运行一个受控的开发命令，不支持 shell 管道。", {
                "command": {"type": "string", "description": "例如 pytest -q 或 npm test"},
                "timeout": {"type": "integer", "description": f"超时秒数，1 到 {self.max_command_timeout}"},
            }, required=["command"]),
            _schema("finish", "任务完成或无法继续时提交最终结果。", {
                "summary": {"type": "string", "description": "面向用户的完成总结"},
                "verification": {"type": "string", "description": "验证方式和结果"},
            }, required=["summary"]),
        ]
        for schema in schemas:
            name = schema["function"]["name"]
            if name in self.blocked_tools:
                schema["function"]["description"] += " 当前为只读模式，此工具已被禁止。"
            elif name in self.forced_approval_tools:
                schema["function"]["description"] += " 当前为安全模式，调用时需要用户单次授权。"
            elif name not in self.allowed_tools:
                schema["function"]["description"] += " 当前 Skill 未默认开放，调用时需要用户单次授权。"
        return schemas

    async def execute(self, name: str, arguments: dict[str, Any], *, approved: bool = False) -> ToolResult:
        if name not in self._handlers:
            return ToolResult(False, f"工具 {name} 不可用", error="工具不存在")
        if name in self.blocked_tools:
            return ToolResult(
                False,
                f"{name} 在只读模式下不可用",
                data={"agent_mode_blocked": True, "agent_mode": self.agent_mode},
                error="当前 Agent 配置为只读模式，不能创建或修改文件",
            )
        if name in self.forced_approval_tools and not approved:
            return ToolResult(
                False,
                f"安全模式要求确认 {name}",
                data={
                    "permission_reason": f"当前 Agent 使用安全模式，执行 {name} 前需要逐次确认。",
                    "risk": "medium",
                    "scope": "exact_action_once",
                },
                error="安全模式要求用户确认",
                approval_required=True,
            )
        if name not in self.allowed_tools and not approved:
            return ToolResult(
                False,
                f"{name} 需要用户授权",
                data={
                    "permission_reason": f"当前 Skill 未默认开放 {name}，需要临时提升工具权限。",
                    "risk": "medium",
                    "scope": "exact_action_once",
                },
                error="当前 Skill 没有权限",
                approval_required=True,
            )
        try:
            if name == "run_command":
                result = self.run_command(**arguments, approved=approved)
            else:
                result = self._handlers[name](**arguments)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except ToolApprovalRequired as exc:
            return ToolResult(
                False,
                f"{name} 需要用户授权",
                data={
                    "permission_reason": exc.reason,
                    "risk": exc.risk,
                    "scope": "exact_action_once",
                },
                error=exc.reason,
                approval_required=True,
            )
        except (TypeError, ValueError, WorkspaceViolation) as exc:
            return ToolResult(False, f"{name} 参数或权限检查失败", error=str(exc))
        except Exception as exc:  # Tool errors must become observations instead of crashing the loop.
            return ToolResult(False, f"{name} 执行失败", error=f"{type(exc).__name__}: {exc}")

    def list_files(self, path: str = ".", max_depth: int = 3) -> ToolResult:
        directory = self.guard.resolve(path)
        if not directory.is_dir():
            raise ValueError("path 必须指向目录")
        max_depth = max(1, min(int(max_depth), 4))
        lines: list[str] = []
        base_depth = len(directory.parts)
        for current, dirs, files in os.walk(directory):
            current_path = Path(current)
            depth = len(current_path.parts) - base_depth
            dirs[:] = sorted(item for item in dirs if item not in SKIPPED_DIRS and not item.startswith(".env"))
            if depth >= max_depth:
                dirs[:] = []
            relative = current_path.relative_to(self.guard.root)
            indent = "  " * depth
            lines.append(f"{indent}{relative.as_posix() if relative.parts else '.'}/")
            for filename in sorted(files):
                if filename.startswith(".env") or filename.endswith((".pem", ".key")):
                    continue
                lines.append(f"{indent}  {filename}")
        text, truncated = _truncate("\n".join(lines))
        return ToolResult(True, f"已列出 {path} 的目录结构", {"output": text}, truncated=truncated)

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> ToolResult:
        target = self.guard.resolve(path)
        if not target.is_file():
            raise ValueError("path 必须指向文件")
        start = max(1, int(start_line))
        requested_end = int(end_line) if end_line is not None else start + 199
        end = max(start, min(requested_end, start + 399))
        raw = target.read_text(encoding="utf-8")
        lines = raw.splitlines()
        selected = lines[start - 1:end]
        numbered = "\n".join(f"{index:4d} | {line}" for index, line in enumerate(selected, start=start))
        text, truncated = _truncate(numbered)
        return ToolResult(
            True,
            f"已读取 {path} 第 {start}–{min(end, len(lines))} 行",
            {"path": path, "start_line": start, "end_line": min(end, len(lines)), "output": text},
            truncated=truncated,
        )

    def search_text(self, query: str, path: str = ".") -> ToolResult:
        if not query:
            raise ValueError("query 不能为空")
        root = self.guard.resolve(path)
        candidates = [root] if root.is_file() else root.rglob("*")
        matches: list[str] = []
        lowered = query.lower()
        for candidate in candidates:
            if not candidate.is_file() or any(part in SKIPPED_DIRS for part in candidate.parts):
                continue
            if candidate.suffix.lower() not in TEXT_SUFFIXES and candidate.name not in {"Dockerfile", "Makefile"}:
                continue
            try:
                for line_number, line in enumerate(candidate.read_text(encoding="utf-8").splitlines(), start=1):
                    if lowered in line.lower():
                        relative = candidate.relative_to(self.guard.root).as_posix()
                        matches.append(f"{relative}:{line_number}: {line.strip()}")
                        if len(matches) >= 50:
                            break
            except UnicodeDecodeError:
                continue
            if len(matches) >= 50:
                break
        output = "\n".join(matches) if matches else "未找到匹配内容"
        return ToolResult(True, f"搜索到 {len(matches)} 条结果", {"query": query, "output": output}, truncated=len(matches) >= 50)

    def create_file(self, path: str, content: str) -> ToolResult:
        target = self.guard.resolve(path, must_exist=False)
        if target.exists():
            raise ValueError("目标文件已存在；请先读取后使用 apply_patch 局部修改")
        if target.suffix.lower() not in TEXT_SUFFIXES and target.name not in {"Dockerfile", "Makefile"}:
            raise ValueError("只允许创建受支持的文本文件类型")
        if "\x00" in content:
            raise ValueError("文件内容不能包含二进制空字节")
        if len(content) > MAX_CREATED_FILE_CHARS:
            raise ValueError(f"单个新文件最多 {MAX_CREATED_FILE_CHARS} 个字符")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("x", encoding="utf-8") as handle:
                handle.write(content)
        except FileExistsError as exc:
            raise ValueError("目标文件已存在；拒绝覆盖") from exc
        diff = "".join(difflib.unified_diff(
            [], content.splitlines(keepends=True), fromfile="/dev/null", tofile=f"b/{path}",
        ))
        diff_text, truncated = _truncate(diff)
        return ToolResult(
            True,
            f"已创建新文件 {path}",
            {"path": path, "diff": diff_text, "created": True, "characters": len(content)},
            truncated=truncated,
        )

    def apply_patch(self, path: str, old_text: str, new_text: str) -> ToolResult:
        target = self.guard.resolve(path)
        if not target.is_file():
            raise ValueError("path 必须指向已有文件")
        if not old_text:
            raise ValueError("old_text 不能为空")
        original = target.read_text(encoding="utf-8")
        count = original.count(old_text)
        if count != 1:
            raise ValueError(f"old_text 应唯一匹配，实际匹配 {count} 次；请重新读取文件后提供更精确的文本")
        updated = original.replace(old_text, new_text, 1)
        diff = "".join(difflib.unified_diff(
            original.splitlines(keepends=True), updated.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}",
        ))
        target.write_text(updated, encoding="utf-8")
        diff_text, truncated = _truncate(diff)
        return ToolResult(True, f"已局部修改 {path}", {"path": path, "diff": diff_text}, truncated=truncated)

    async def run_command(self, command: str, timeout: int = 30, *, approved: bool = False) -> ToolResult:
        args = _validate_command(command, approved=approved)
        if Path(args[0]).name == "pytest":
            args = [sys.executable, "-m", "pytest", *args[1:]]
        timeout = max(1, min(int(timeout), self.max_command_timeout))
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=self.guard.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return ToolResult(False, f"命令超时（{timeout}s）", {"command": command}, error="command timed out")
        output = stdout.decode("utf-8", errors="replace")
        output, truncated = _truncate(output)
        ok = process.returncode == 0
        return ToolResult(
            ok,
            f"命令执行{'成功' if ok else '失败'}，退出码 {process.returncode}",
            {"command": command, "exit_code": process.returncode, "output": output},
            error=None if ok else f"command exited with code {process.returncode}",
            truncated=truncated,
        )

    def finish(self, summary: str, verification: str = "") -> ToolResult:
        if not summary.strip():
            raise ValueError("summary 不能为空")
        return ToolResult(True, "Agent 已提交最终结果", {"summary": summary.strip(), "verification": verification.strip()})


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_TEXT_CHARS:
        return text, False
    head = text[: MAX_TEXT_CHARS // 2]
    tail = text[-MAX_TEXT_CHARS // 2 :]
    return f"{head}\n\n... 输出已截断 ...\n\n{tail}", True


def _validate_command(command: str, *, approved: bool = False) -> list[str]:
    if not command.strip():
        raise ValueError("command 不能为空")
    try:
        args = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"命令解析失败：{exc}") from exc
    if not args:
        raise ValueError("command 不能为空")
    executable = Path(args[0]).name
    allowed = {"python", "python3", "pytest", "npm", "node", "ruff", "mypy", "git"}
    if executable in {"sh", "bash", "zsh", "fish", "cmd", "powershell", "pwsh"}:
        raise ValueError("不允许启动 Shell 解释器")
    if any(arg in {"..", "-rf", "--force", "--delete"} for arg in args[1:]):
        raise ValueError("命令包含高风险参数")
    if not approved:
        if executable not in allowed:
            raise ToolApprovalRequired(f"命令 {executable} 不在默认允许列表中", risk="high")
        if executable in {"python", "python3", "node"} and any(arg in {"-c", "-e", "--eval"} for arg in args[1:]):
            raise ToolApprovalRequired("命令包含内联脚本，需要审查具体内容", risk="high")
        if executable == "git" and (len(args) < 2 or args[1] not in {"status", "diff", "log", "show"}):
            raise ToolApprovalRequired("命令会修改 Git 状态", risk="high")
        if executable == "npm" and (len(args) < 2 or args[1] not in {"test", "run"}):
            raise ToolApprovalRequired("命令可能安装依赖或修改项目配置", risk="high")
    return args
