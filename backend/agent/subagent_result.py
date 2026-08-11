"""Structured subagent / crew return shape (GPT-audit P2).

Normalize free-form tool text into a compact dict the parent model can use
without ingesting huge logs.
"""
from __future__ import annotations

import json
import re
from typing import Any


def structure_subagent_result(
    raw: str | dict[str, Any] | None,
    *,
    default_summary: str = "",
) -> dict[str, Any]:
    """Return {summary, findings, changed_files, tests, blockers, recommended_next_action}."""
    out: dict[str, Any] = {
        "summary": default_summary or "",
        "findings": [],
        "changed_files": [],
        "tests": [],
        "blockers": [],
        "recommended_next_action": "",
    }
    if raw is None:
        return out
    if isinstance(raw, dict):
        for k in out:
            if k in raw and raw[k] is not None:
                out[k] = raw[k]
        if not out["summary"] and raw.get("message"):
            out["summary"] = str(raw["message"])[:800]
        return out

    text = str(raw).strip()
    if not text:
        return out
    # Try JSON block
    try:
        if text.startswith("{"):
            data = json.loads(text)
            if isinstance(data, dict):
                return structure_subagent_result(data, default_summary=default_summary)
    except Exception:
        pass
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict):
                return structure_subagent_result(data, default_summary=default_summary)
        except Exception:
            pass

    out["summary"] = text[:800]
    # Heuristic blockers
    if re.search(r"(?i)(blocked|error|failed|失败|阻塞)", text):
        out["blockers"].append(text[:200])
    return out


def format_structured_for_parent(data: dict[str, Any]) -> str:
    """One compact block for the parent agent context."""
    lines = ["[Subagent result]"]
    if data.get("summary"):
        lines.append(f"summary: {data['summary']}")
    if data.get("findings"):
        lines.append("findings:")
        for f in data["findings"][:8]:
            lines.append(f"  - {f}")
    if data.get("changed_files"):
        lines.append("changed_files: " + ", ".join(str(x) for x in data["changed_files"][:12]))
    if data.get("tests"):
        lines.append(f"tests: {data['tests'][:5]}")
    if data.get("blockers"):
        lines.append("blockers: " + "; ".join(str(b) for b in data["blockers"][:5]))
    if data.get("recommended_next_action"):
        lines.append(f"next: {data['recommended_next_action']}")
    return "\n".join(lines)
