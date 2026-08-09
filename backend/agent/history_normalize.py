"""Normalize tool_call / tool_result pairs before every LLM sample (Codex-style).

Orphan tool results (no matching assistant tool_calls) and dangling tool_calls
(no result yet / lost mid-compress) poison both API validity and model quality.
API-edge drop in openai_compatible is too late — run this earlier.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _call_ids_from_assistant(msg: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    tcs = msg.get("tool_calls")
    if not isinstance(tcs, list):
        return ids
    for tc in tcs:
        if not isinstance(tc, dict):
            continue
        tid = tc.get("id") or tc.get("tool_call_id")
        if tid:
            ids.append(str(tid))
    return ids


def normalize_history_for_llm(
    messages: list[dict[str, Any]],
    *,
    synthetic_prefix: str = "[aborted]",
) -> list[dict[str, Any]]:
    """Return a new list safe to send to providers.

    1. Collect all tool_call ids declared by assistant messages.
    2. Drop tool messages whose tool_call_id is unknown.
    3. For each declared id without a tool result, append a synthetic tool
       message so the next sample is pair-complete.
    """
    if not messages:
        return messages

    out: list[dict[str, Any]] = []
    declared: set[str] = set()
    for m in messages:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "assistant":
            for tid in _call_ids_from_assistant(m):
                declared.add(tid)
        out.append(m)

    have_result: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    dropped = 0
    for m in out:
        if m.get("role") != "tool":
            cleaned.append(m)
            continue
        tid = str(m.get("tool_call_id") or m.get("id") or "").strip()
        if not tid:
            # nameless tool row — keep if no pairing possible, else drop
            dropped += 1
            continue
        if tid not in declared:
            dropped += 1
            continue
        have_result.add(tid)
        cleaned.append(m)

    synthesized = 0
    # Walk cleaned to find last assistant with tool_calls; append missing after it
    # Simpler: append all missing synthetic tools at end (OpenAI accepts order as long as ids match)
    missing = [tid for tid in declared if tid not in have_result]
    # Prefer insert after the assistant that declared them
    if missing:
        # Map id → name from last assistant that declared it
        id_to_name: dict[str, str] = {}
        for m in cleaned:
            if m.get("role") != "assistant":
                continue
            tcs = m.get("tool_calls")
            if not isinstance(tcs, list):
                continue
            for tc in tcs:
                if not isinstance(tc, dict):
                    continue
                tid = str(tc.get("id") or tc.get("tool_call_id") or "")
                if not tid:
                    continue
                name = ""
                fn = tc.get("function")
                if isinstance(fn, dict):
                    name = str(fn.get("name") or "")
                name = name or str(tc.get("name") or "tool")
                id_to_name[tid] = name

        # Insert synthetics immediately after the last assistant that has tool_calls
        last_asst_i = -1
        for i, m in enumerate(cleaned):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                last_asst_i = i
        synth_msgs = []
        for tid in missing:
            synth_msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": tid,
                    "name": id_to_name.get(tid, "tool"),
                    "content": (
                        f"{synthetic_prefix} tool result missing for call_id={tid} "
                        "(timeout, cancel, or history repair)."
                    ),
                }
            )
            synthesized += 1
        if last_asst_i >= 0:
            cleaned = (
                cleaned[: last_asst_i + 1]
                + synth_msgs
                + cleaned[last_asst_i + 1 :]
            )
        else:
            cleaned.extend(synth_msgs)

    if dropped or synthesized:
        logger.info(
            "normalize_history_for_llm dropped_orphan=%s synthesized_missing=%s",
            dropped,
            synthesized,
        )
    return cleaned
