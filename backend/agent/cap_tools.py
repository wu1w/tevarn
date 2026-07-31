"""P0 / K-03：按 kernel 进程能力裁剪 LLM 可见 tools（不可见 + 不可调）。

抽出 loop 内逻辑，供初始 load 与 use_tool_pack 扩容后复用。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def filter_tools_for_process(
    tools: list[dict[str, Any]],
    process: Any | None,
    *,
    fail_closed_on_error: bool = True,
) -> list[dict[str, Any]]:
    """Return tools allowed by process capabilities.

    - No process → unchanged (caller may not be on kernel path).
    - capabilities is None → unchanged (legacy full open).
    - capabilities set → only matching tools (empty list if none match).
    - filter failure with explicit caps → empty list when fail_closed_on_error.
    """
    if process is None or not getattr(process, "id", None):
        return tools

    caps = getattr(process, "capabilities", None)
    try:
        from backend.kernel import get_kernel

        k = get_kernel()
        names = [
            (t.get("function") or {}).get("name")
            for t in tools
            if (t.get("function") or {}).get("name")
        ]
        names = [n for n in names if n]
        filtered_names: list[str] | None = None
        if hasattr(k, "filter_tools"):
            filtered_names = k.filter_tools(str(process.id), names)
        elif caps is not None:
            from backend.agent.grant_store import tool_matches_crew_caps

            filtered_names = [
                n for n in names if tool_matches_crew_caps(n, caps)
            ]
        if filtered_names is not None:
            allow = set(filtered_names)
            before = len(tools)
            out = [
                t
                for t in tools
                if (t.get("function") or {}).get("name") in allow
            ]
            if before != len(out):
                logger.info(
                    "cap_tools trim process=%s %s→%s",
                    str(process.id)[:8],
                    before,
                    len(out),
                )
            return out
        if caps is not None:
            from backend.agent.grant_store import tool_matches_crew_caps

            allow = {n for n in names if tool_matches_crew_caps(n, caps)}
            return [
                t
                for t in tools
                if (t.get("function") or {}).get("name") in allow
            ]
        return tools
    except Exception as e:
        if caps is not None and fail_closed_on_error:
            logger.warning(
                "cap_tools filter failed under explicit caps — fail-closed empty: %s",
                e,
            )
            return []
        logger.debug("cap_tools filter skip: %s", e)
        return tools
