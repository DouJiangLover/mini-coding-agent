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

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "data": self.data,
            "error": self.error,
            "truncated": self.truncated,
        }


class ToolRegistry:
    def __init__(self, workspace: Path, allowed_tools: list[str] | None = None) -> None:
        self.guard = WorkspaceGuard(workspace)
        self.allowed_tools = set(allowed_tools or self.available_names())
        self._handlers: dict[str, Callable[..., Any]] = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "search_text": self.search_text,
            "apply_patch": self.apply_patch,
            "run_command": self.run_command,
            "finish": self.finish,
        }

    @staticmethod
    def available_names() -> list[str]:
        return ["list_files", "read_file", "search_text", "apply_patch", "run_command", "finish"]

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
            _schema("apply_patch", "把文件中唯一匹配的 old_text 替换为 new_text，并返回 diff。", {
                "path": {"type": "string", "description": "相对文件路径"},
                "old_text": {"type": "string", "description": "文件中必须唯一出现的原文本"},
                "new_text": {"type": "string", "description": "替换后的文本"},
            }, required=["path", "old_text", "new_text"]),
            _schema("run_command", "在工作区运行一个受控的开发命令，不支持 shell 管道。", {
                "command": {"type": "string", "description": "例如 pytest -q 或 npm test"},
                "timeout": {"type": "integer", "description": "超时秒数，1 到 60，默认 30"},
            }, required=["command"]),
            _schema("finish", "任务完成或无法继续时提交最终结果。", {
                "summary": {"type": "string", "description": "面向用户的完成总结"},
                "verification": {"type": "string", "description": "验证方式和结果"},
            }, required=["summary"]),
        ]
        return [schema for schema in schemas if schema["function"]["name"] in self.allowed_tools]

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        if name not in self.allowed_tools or name not in self._handlers:
            return ToolResult(False, f"工具 {name} 不可用", error="工具不存在或当前 Skill 没有权限")
        try:
            result = self._handlers[name](**arguments)
            if asyncio.iscoroutine(result):
                result = await result
            return result
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

    async def run_command(self, command: str, timeout: int = 30) -> ToolResult:
        args = _validate_command(command)
        if Path(args[0]).name == "pytest":
            args = [sys.executable, "-m", "pytest", *args[1:]]
        timeout = max(1, min(int(timeout), 60))
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


def _validate_command(command: str) -> list[str]:
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
    if executable not in allowed:
        raise ValueError(f"命令 {executable} 不在允许列表中")
    if executable in {"python", "python3", "node"} and any(arg in {"-c", "-e", "--eval"} for arg in args[1:]):
        raise ValueError("不允许执行内联脚本")
    if executable == "git" and (len(args) < 2 or args[1] not in {"status", "diff", "log", "show"}):
        raise ValueError("只允许只读 git 命令")
    if executable == "npm" and (len(args) < 2 or args[1] not in {"test", "run"}):
        raise ValueError("只允许 npm test 或 npm run <script>")
    if any(arg in {"..", "-rf", "--force", "--delete"} for arg in args[1:]):
        raise ValueError("命令包含高风险参数")
    return args
