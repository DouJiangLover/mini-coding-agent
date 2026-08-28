import asyncio
from pathlib import Path

from backend.tools.registry import ToolRegistry


def test_apply_patch_returns_unified_diff(tmp_path: Path):
    source = tmp_path / "main.py"
    source.write_text("def answer():\n    return 41\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path)

    result = registry.apply_patch("main.py", "    return 41\n", "    return 42\n")

    assert result.ok
    assert "+    return 42" in result.data["diff"]
    assert source.read_text(encoding="utf-8").endswith("return 42\n")


def test_create_file_creates_parent_and_diff(tmp_path: Path):
    registry = ToolRegistry(tmp_path)

    result = registry.create_file("src/main.js", "export const answer = 42;\n")

    assert result.ok
    assert result.data["created"] is True
    assert "+export const answer = 42;" in result.data["diff"]
    assert (tmp_path / "src" / "main.js").read_text(encoding="utf-8") == "export const answer = 42;\n"


def test_create_file_refuses_to_overwrite(tmp_path: Path):
    source = tmp_path / "index.html"
    source.write_text("original", encoding="utf-8")
    registry = ToolRegistry(tmp_path)

    result = asyncio.run(registry.execute("create_file", {"path": "index.html", "content": "replacement"}))

    assert not result.ok
    assert "已存在" in (result.error or "")
    assert source.read_text(encoding="utf-8") == "original"


def test_create_file_rejects_credentials(tmp_path: Path):
    registry = ToolRegistry(tmp_path)

    result = asyncio.run(registry.execute("create_file", {"path": ".env", "content": "SECRET=value"}))

    assert not result.ok
    assert "安全策略" in (result.error or "")


def test_apply_patch_rejects_ambiguous_match(tmp_path: Path):
    source = tmp_path / "main.py"
    source.write_text("same\nsame\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path)

    result = asyncio.run(registry.execute("apply_patch", {
        "path": "main.py", "old_text": "same", "new_text": "changed",
    }))

    assert not result.ok
    assert "实际匹配 2 次" in (result.error or "")


def test_command_rejects_shell(tmp_path: Path):
    registry = ToolRegistry(tmp_path)

    result = asyncio.run(registry.execute("run_command", {"command": "sh -c pwd"}))

    assert not result.ok
    assert "允许列表" in (result.error or "")
