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
