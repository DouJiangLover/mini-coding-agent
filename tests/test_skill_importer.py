import io
import json
import zipfile

import pytest

from backend.skills.importer import SkillImportError, parse_skill_upload


def test_imports_skill_markdown_with_frontmatter_and_external_tool_names():
    content = b"""---
name: Performance Audit
description: Finds repeatable performance bottlenecks before changing code.
keywords: [performance, benchmark]
allowed-tools: [Read, Grep, Bash, Edit]
---
# Performance Audit

Run a baseline benchmark first, identify the measured bottleneck, then repeat the same benchmark after a focused change.
"""

    imported = parse_skill_upload("SKILL.md", content)

    assert imported.display_name == "Performance Audit"
    assert imported.keywords == ["performance", "benchmark"]
    assert imported.allowed_tools == ["read_file", "search_text", "run_command", "apply_patch", "finish"]
    assert "baseline benchmark" in imported.prompt
    assert imported.source_format == "SKILL.md"


def test_imports_standalone_json_skill():
    content = json.dumps({
        "display_name": "API Review Skill",
        "description": "Reviews API changes for compatibility and missing tests.",
        "trigger_keywords": ["API", "compatibility"],
        "tools": ["list_files", "read_file", "search_text"],
        "instructions": "Inspect the public API and its tests, then report compatibility risks with file evidence.",
    }).encode()

    imported = parse_skill_upload("api-review.json", content)

    assert imported.display_name == "API Review Skill"
    assert imported.keywords == ["API", "compatibility"]
    assert imported.allowed_tools == ["list_files", "read_file", "search_text", "finish"]
    assert imported.source_format == "JSON"


def test_imports_zip_package_without_extracting_other_files():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("release/skill.json", json.dumps({
            "name": "Release Checklist Skill",
            "description": "Checks release readiness using project evidence.",
            "keywords": ["release", "publish"],
            "allowed_tools": ["list_files", "read_file", "run_command", "finish"],
        }))
        archive.writestr("release/prompt.md", "Read the release configuration, run existing checks, and report every blocking failure before finishing.")
        archive.writestr("release/scripts/ignored.sh", "echo this file is never extracted or executed")

    imported = parse_skill_upload("release-skill.zip", stream.getvalue())

    assert imported.display_name == "Release Checklist Skill"
    assert imported.source_format == "ZIP"
    assert "blocking failure" in imported.prompt


def test_rejects_zip_with_path_traversal():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("../SKILL.md", "# Unsafe Skill\n\nThis archive must not be imported because its path escapes the package.")

    with pytest.raises(SkillImportError, match="越界路径"):
        parse_skill_upload("unsafe.zip", stream.getvalue())


def test_rejects_unsupported_skill_file_type():
    with pytest.raises(SkillImportError, match="仅支持"):
        parse_skill_upload("skill.py", b"print('unsafe')")
