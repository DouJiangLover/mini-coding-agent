from pathlib import Path

import pytest

from backend.skills.router import SkillRouter


SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"


def test_router_selects_bug_fix_skill():
    match = SkillRouter(SKILLS_ROOT).select("请修复 pytest 失败的测试")

    assert match.skill.name == "bug_fix"
    assert "修复" in match.matched_keywords


def test_router_selects_documentation_skill():
    match = SkillRouter(SKILLS_ROOT).select("为项目编写 README 使用文档")

    assert match.skill.name == "documentation"


def test_router_selects_frontend_build_skill():
    match = SkillRouter(SKILLS_ROOT).select("请根据需求文档从零实现一个 2048 小游戏")

    assert match.skill.name == "frontend_build"
    assert "2048" in match.matched_keywords


def test_router_persists_disabled_skill(tmp_path: Path):
    config = tmp_path / "skill-config.json"
    router = SkillRouter(SKILLS_ROOT, config)

    updated = router.set_enabled("bug_fix", False)

    assert updated["enabled"] is False
    restored_bug_fix = next(item for item in SkillRouter(SKILLS_ROOT, config).list_public() if item["name"] == "bug_fix")
    assert restored_bug_fix["enabled"] is False
    assert router.select("请修复 pytest 失败的测试").skill.name != "bug_fix"


def test_router_creates_and_routes_to_custom_skill(tmp_path: Path):
    config = tmp_path / "skill-config.json"
    router = SkillRouter(SKILLS_ROOT, config)

    created = router.create_custom(
        display_name="性能分析 Skill",
        description="分析性能瓶颈并建立可重复的基准测试",
        keywords=["benchmark", "性能分析"],
        allowed_tools=["list_files", "read_file", "search_text", "run_command", "finish"],
        prompt="先读取性能相关实现，建立可重复基线，再定位热点并用相同命令复测。",
    )

    assert created["source"] == "custom"
    assert created["enabled"] is True
    restored = SkillRouter(SKILLS_ROOT, config)
    assert restored.select("请做 benchmark 和性能分析").skill.name == created["name"]
    assert any(item["name"] == created["name"] for item in restored.list_public())


def test_router_keeps_at_least_one_skill_enabled(tmp_path: Path):
    router = SkillRouter(SKILLS_ROOT, tmp_path / "skill-config.json")
    enabled_names = [skill.name for skill in router.enabled_skills]
    for name in enabled_names[:-1]:
        router.set_enabled(name, False)

    with pytest.raises(ValueError, match="至少需要保留一个"):
        router.set_enabled(enabled_names[-1], False)
