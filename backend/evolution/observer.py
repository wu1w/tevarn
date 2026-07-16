"""P4 Auto-observe: cluster tool sequences and nudge skill creation."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from backend.evolution import store
from backend.evolution.config import get_evolution_config
from backend.evolution.improver import classify_failures, propose_skill_from_failure, text_similarity

logger = logging.getLogger(__name__)


def fingerprint_tools(tools: list[dict[str, Any]]) -> str:
    names = [str(t.get("name") or "?") for t in tools][:20]
    raw = ">".join(names)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def record_observation(
    session_id: str,
    *,
    tools: list[dict[str, Any]],
    final_content: str = "",
    user_input: str = "",
) -> dict[str, Any] | None:
    cfg = get_evolution_config()
    if not cfg.enabled or not cfg.auto_observe:
        return None
    if cfg.observe_nudge_level == "off":
        return None
    if not tools:
        return None

    failures = classify_failures(tool_trace=tools, final_content=final_content)
    fp = fingerprint_tools(tools)
    store.add_observation(
        session_id=session_id,
        fingerprint=fp,
        tools=tools,
        user_input=user_input,
        final_content=final_content,
        failure_codes=failures,
    )
    cluster = store.bump_cluster(fp, session_id=session_id, sample_input=user_input)

    result: dict[str, Any] = {
        "fingerprint": fp,
        "failures": failures,
        "cluster_count": cluster.get("hit_count", 0) if cluster else 0,
        "nudge": None,
        "asset": None,
    }

    min_n = max(2, int(cfg.observe_min_sessions))
    hits = int(cluster.get("hit_count") or 0) if cluster else 0
    # Pattern: repeated sessions with missing verification or tool errors
    interesting = bool(set(failures) & {"missing_verification", "tool_error", "schema_mismatch", "empty_result"})
    if hits >= min_n and interesting and not cluster.get("skill_created"):
        proposal = propose_skill_from_failure(
            user_input=user_input or cluster.get("sample_input") or f"pattern {fp}",
            failure_codes=failures or ["pattern"],
            tool_trace=tools,
            final_content=final_content,
            source_label=f"observe:{fp}",
        )
        # mark so we don't spam
        store.mark_cluster_skill(fp, proposal["name"])
        result["nudge"] = {
            "level": cfg.observe_nudge_level,
            "message": (
                f"HAEE/TEE 注意到相似会话已出现 {hits} 次（{', '.join(failures) or 'pattern'}），"
                f"已准备 skill 草案 `{proposal['name']}`。"
            ),
            "proposal_name": proposal["name"],
        }
        result["proposal"] = proposal
    return result
