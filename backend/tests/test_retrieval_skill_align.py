"""检索分阈值 + prompt-skill 场景对齐。"""
from __future__ import annotations

from backend.agent.tool_policy import (
    SCENE_SKILL_HINTS,
    injection_knobs,
    infer_scene,
)
from backend.services.rag.context_assembler import ContextAssembler, RetrievalContract
from backend.services.reranker.interface import RerankedResult
from backend.services.skill_store.prompt_skill_loader import PromptSkill, PromptSkillLoader


def test_injection_knobs_thresholds():
    m = injection_knobs("minimal")
    assert m["rag"] is False
    assert m["prompt_skills"] is False
    assert m["skill_max_full"] == 0

    s = injection_knobs("standard")
    assert s["rag"] is True
    assert float(s["rag_min_score"]) >= 0.55
    assert int(s["skill_max_full"]) == 1
    assert float(s["skill_threshold"]) >= 0.9

    r = injection_knobs("rich")
    assert float(r["rag_min_score"]) < float(s["rag_min_score"])
    assert int(r["skill_max_full"]) >= 2


def test_assembler_respects_min_score():
    results = [
        RerankedResult(text="high", score=0.9, original_index=0),
        RerankedResult(text="low", score=0.2, original_index=1),
    ]
    high_only = ContextAssembler(RetrievalContract(min_score=0.5)).assemble(results)
    assert "high" in high_only
    assert "low" not in high_only

    empty = ContextAssembler(RetrievalContract(min_score=0.95)).assemble(results)
    assert empty == ""


def _skill(name: str, desc: str, tags: list[str] | None = None) -> PromptSkill:
    return PromptSkill(
        source="test",
        name=name,
        display_name=name,
        description=desc,
        body=desc,
        full_content=f"---\nname: {name}\n---\n{desc}",
        path=f"/tmp/{name}.md",
        size=100,
        tags=tags or [],
    )


def test_scene_pack_boosts_skill_score():
    loader = PromptSkillLoader()
    desktop_skill = _skill("ui-click", "Automate desktop GUI clicks", tags=["desktop", "gui"])
    other = _skill("cooking", "How to cook pasta", tags=["food"])

    base = loader.score_relevance(desktop_skill, "帮我点一下按钮")
    boosted = loader.score_relevance(
        desktop_skill, "帮我点一下按钮", scene_packs=["desktop"]
    )
    assert boosted.score > base.score
    assert any("scene:desktop" in r for r in boosted.reasons)

    other_b = loader.score_relevance(other, "帮我点一下按钮", scene_packs=["desktop"])
    assert boosted.score > other_b.score


def test_select_full_prefers_scene_aligned():
    loader = PromptSkillLoader()
    skills = [
        _skill("ui-click", "desktop GUI automation click screenshot", tags=["desktop"]),
        _skill("random", "unrelated hobby knitting", tags=["hobby"]),
    ]
    selected = loader.select_full_skills(
        skills,
        "请用桌面自动化点那个确认按钮",
        mode="auto",
        max_full=1,
        threshold=0.5,
        scene_packs=["desktop"],
    )
    assert selected
    assert selected[0].skill.name == "ui-click"


def test_build_injection_disabled():
    loader = PromptSkillLoader()
    block, plan = loader.build_injection_block(
        "x",
        skills=[_skill("a", "b")],
        enabled=False,
    )
    assert block == ""
    assert plan.mode == "off"


def test_greeting_scene_disables_skills_via_knobs():
    plan = infer_scene("你好", profile="dynamic")
    kn = injection_knobs(plan.injection_tier)
    assert plan.injection_tier == "minimal"
    assert kn["prompt_skills"] is False


def test_scene_skill_hints_cover_main_packs():
    for p in ("coding", "desktop", "manage", "evolution", "office"):
        assert p in SCENE_SKILL_HINTS
