import asyncio
from pathlib import Path

from backend.tools.registry import ToolRegistry, ToolResult
from backend.traceability import TraceabilityLedger


def criteria() -> list[dict[str, str]]:
    return [
        {
            "id": "AC-01",
            "description": "用户可以创建卡片",
            "priority": "must",
            "verification": "automated_test",
        },
        {
            "id": "AC-02",
            "description": "空状态布局清晰",
            "priority": "must",
            "verification": "human_review",
        },
    ]


def test_ledger_requires_fresh_evidence_after_each_change() -> None:
    ledger = TraceabilityLedger(criteria())

    assert not ledger.observe(
        "create_file",
        {"path": "unassigned.py"},
        ToolResult(True, "已创建 unassigned.py", {"path": "unassigned.py"}),
    )
    assert all(item["status"] == "pending" for item in ledger.public_dict()["requirements"])

    assert ledger.observe(
        "create_file",
        {"path": "app.py", "requirement_ids": ["AC-01"]},
        ToolResult(True, "已创建 app.py", {"path": "app.py"}),
    )
    assert ledger.public_dict()["requirements"][0]["status"] == "implemented"

    # 环境探测不是需求验证，不能把验收项误标为通过。
    assert not ledger.observe(
        "run_command",
        {"command": "python --version", "requirement_ids": ["AC-01"]},
        ToolResult(True, "命令执行成功", {"command": "python --version", "exit_code": 0}),
    )
    assert ledger.public_dict()["verified"] == 0

    assert ledger.observe(
        "run_command",
        {"command": "python -m pytest -q", "requirement_ids": ["AC-01"]},
        ToolResult(True, "测试通过", {"command": "python -m pytest -q", "exit_code": 0}),
    )
    assert ledger.public_dict()["requirements"][0]["status"] == "verified"

    assert ledger.observe(
        "apply_patch",
        {"path": "app.py", "requirement_ids": ["AC-01"]},
        ToolResult(True, "已修改 app.py", {"path": "app.py"}),
    )
    snapshot = ledger.public_dict()
    assert snapshot["verified"] == 0
    assert snapshot["requirements"][0]["status"] == "implemented"


def test_human_review_and_snapshot_restore() -> None:
    ledger = TraceabilityLedger(criteria())
    ledger.observe(
        "create_file",
        {"path": "index.html", "requirement_ids": ["AC-02"]},
        ToolResult(True, "已创建 index.html", {"path": "index.html"}),
    )
    ledger.observe(
        "read_file",
        {"path": "index.html", "requirement_ids": ["AC-02"]},
        ToolResult(True, "已读取 index.html", {"path": "index.html"}),
    )

    restored = TraceabilityLedger(criteria(), ledger.public_dict())

    assert restored.public_dict()["requirements"][1]["status"] == "verified"
    assert restored.public_dict()["requirements"][1]["evidence"][-1]["evidence_type"] == "review"
    assert restored.blocking_gaps()[0]["requirement_id"] == "AC-01"


def test_registry_accepts_trace_metadata_without_passing_it_to_handler(tmp_path: Path) -> None:
    registry = ToolRegistry(tmp_path, requirement_ids=["AC-01"])

    result = asyncio.run(registry.execute("create_file", {
        "path": "feature.py",
        "content": "VALUE = 1\n",
        "requirement_ids": ["AC-01"],
    }))

    assert result.ok
    assert (tmp_path / "feature.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    create_schema = next(
        schema for schema in registry.schemas()
        if schema["function"]["name"] == "create_file"
    )
    requirement_schema = create_schema["function"]["parameters"]["properties"]["requirement_ids"]
    assert requirement_schema["items"]["enum"] == ["AC-01"]
