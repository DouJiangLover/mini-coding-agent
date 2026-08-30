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
    assert not result.approval_required
    assert "Shell" in (result.error or "")


def test_command_outside_allowlist_requests_approval(tmp_path: Path):
    registry = ToolRegistry(tmp_path)

    result = asyncio.run(registry.execute("run_command", {"command": "pip --version"}))

    assert not result.ok
    assert result.approval_required
    assert result.data["risk"] == "high"


def test_skill_permission_can_be_approved_for_one_exact_action(tmp_path: Path):
    registry = ToolRegistry(tmp_path, allowed_tools=["read_file"])
    arguments = {"path": "src/audit.py", "content": "def record():\n    return True\n"}

    blocked = asyncio.run(registry.execute("create_file", arguments))

    assert blocked.approval_required
    assert blocked.data["scope"] == "exact_action_once"
    assert not (tmp_path / "src" / "audit.py").exists()

    allowed = asyncio.run(registry.execute("create_file", arguments, approved=True))

    assert allowed.ok
    assert (tmp_path / "src" / "audit.py").is_file()


def test_safe_mode_requires_confirmation_for_write_tools(tmp_path: Path):
    source = tmp_path / "main.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path, agent_mode="safe")
    arguments = {"path": "main.py", "old_text": "VALUE = 1", "new_text": "VALUE = 2"}

    blocked = asyncio.run(registry.execute("apply_patch", arguments))

    assert blocked.approval_required
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    allowed = asyncio.run(registry.execute("apply_patch", arguments, approved=True))
    assert allowed.ok
    assert source.read_text(encoding="utf-8") == "VALUE = 2\n"


def test_read_only_mode_blocks_file_changes_even_when_approved(tmp_path: Path):
    registry = ToolRegistry(tmp_path, agent_mode="read_only")
    arguments = {"path": "created.py", "content": "VALUE = 1\n"}

    result = asyncio.run(registry.execute("create_file", arguments, approved=True))

    assert not result.ok
    assert result.data["agent_mode_blocked"] is True
    assert not (tmp_path / "created.py").exists()


def test_autonomous_mode_opens_safe_skill_outside_tools(tmp_path: Path):
    registry = ToolRegistry(tmp_path, allowed_tools=["read_file", "finish"], agent_mode="autonomous")

    result = asyncio.run(registry.execute("create_file", {"path": "created.py", "content": "VALUE = 1\n"}))

    assert result.ok
    assert (tmp_path / "created.py").is_file()
