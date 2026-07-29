"""协议 0.1 + 治理骨架：无 DB 纯逻辑 + 轻量构造。"""

from __future__ import annotations

from types import SimpleNamespace

from backend.kernel.governance import (
    assert_research_invariants,
    build_governance_export,
    build_kernel_surface_export,
    red_line_by_id,
)
from backend.kernel.protocol_spec import (
    PROTOCOL_VERSION,
    identity_to_agent_card,
    parse_task_envelope,
    protocol_manifest,
)


def test_research_invariants_ok() -> None:
    assert assert_research_invariants() == []


def test_protocol_manifest_has_concepts() -> None:
    m = protocol_manifest()
    assert m["protocol_version"] == PROTOCOL_VERSION
    assert "employee" in m["product_concepts"]
    assert "job" in m["product_concepts"]
    assert "approval" in m["product_concepts"]
    assert "agent_card" in m["interop"]
    assert "multi-tenant SaaS" in " ".join(m["non_goals"])


def test_agent_card_from_identity() -> None:
    ident = SimpleNamespace(
        id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        name="研究员",
        role="research",
        status="active",
        capabilities=["file_rw", "web_search", "custom_cap"],
        default_token_budget=40_000,
        credit_score=80,
        sub_agent_id=None,
    )
    mem = [
        SimpleNamespace(kind="persona", content="好奇"),
        SimpleNamespace(kind="duty", content="检索与综合"),
    ]
    card = identity_to_agent_card(ident, memory_entries=mem)
    d = card.to_dict()
    assert d["kind"] == "agent_card"
    assert d["name"] == "研究员"
    assert d["takton"]["product_concept"] == "employee"
    skill_ids = {s["id"] for s in d["skills"]}
    assert "web_search" in skill_ids
    assert "custom_cap" in skill_ids
    # extension tagged
    custom = next(s for s in d["skills"] if s["id"] == "custom_cap")
    assert "extension" in custom["tags"]


def test_parse_task_envelope_instruction() -> None:
    env = parse_task_envelope(
        {
            "instruction": "写一份摘要",
            "identity_name": "工程师",
            "priority": 3,
            "source": "a2a",
        }
    )
    assert env.text == "写一份摘要"
    assert env.identity_name == "工程师"
    assert env.priority == 3
    assert env.source == "api"  # inbox whitelist
    assert env.metadata.get("a2a_source") == "a2a"


def test_parse_task_envelope_parts() -> None:
    env = parse_task_envelope(
        {
            "message_id": "m1",
            "parts": [{"type": "text", "text": "hello job"}],
            "metadata": {"identity_id": "id-1"},
        }
    )
    assert env.message_id == "m1"
    assert env.text == "hello job"
    assert env.identity_id == "id-1"


def test_parse_task_envelope_requires_target() -> None:
    try:
        parse_task_envelope({"instruction": "x"})
        assert False, "should raise"
    except ValueError as e:
        assert "identity" in str(e).lower()


def test_governance_export_red_lines() -> None:
    g = build_governance_export()
    assert g["kind"] == "governance_manifest"
    ids = {r["id"] for r in g["red_lines"]}
    assert "evolution_human_approval" in ids
    assert "capability_narrow_only" in ids
    assert g["invariants"]["auto_apply_evolution_caps"] is False
    assert red_line_by_id("inbox_bounded") is not None


def test_kernel_surface_layers() -> None:
    s = build_kernel_surface_export()
    assert "kernel" in s["layers"]
    assert "inbox" in s["layers"]
    assert "protocol" in s["layers"]
    assert "employee" in s["product_concepts"]
