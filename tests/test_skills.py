from pathlib import Path

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
