"""正文伪 toolcall 回收：模型把工具写进 content 时二次解析为 native ToolCall。

策略：
A) 可解析白名单工具 → 转 ToolCall，正文剥离伪形态
B) 像 tool 但解析失败 → 泄漏计数 + nudge / force_final
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# 仅回收「配置/运维」类低危工具；command/file_write 等永不从正文执行
RECOVER_WHITELIST: frozenset[str] = frozenset(
    {
        "manage_mcp",
        "update_config",
        "configure_tevarn",
        "use_tool_pack",
        "clarify",
        "current_time",
        "get_system_status",
        "list_available_models",
        "capability_status",
        "manage_goal",
    }
)

# args 组只定位起点 {，完整对象由括号平衡解析
_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"<tool_call>\s*"
        r"(?:<tool_name>|name\s*[:=]\s*)?[`\"']?(?P<name>[A-Za-z_][\w.-]{1,64})[`\"']?"
        r"[\s\S]{0,200}?(?P<args>\{)",
        re.I,
    ),
    re.compile(
        r"```(?:json|tool|tool_call)?\s*"
        r"\{[\s\S]{0,80}?\"name\"\s*:\s*\"(?P<name>[A-Za-z_][\w.-]{1,64})\""
        r"[\s\S]{0,120}?\"(?:arguments|parameters|input)\"\s*:\s*(?P<args>\{)",
        re.I,
    ),
    re.compile(
        r"(?:invoke\s+tool|call\s+tool|function\s*call)\s+"
        r"[`\"']?(?P<name>[A-Za-z_][\w.-]{1,64})[`\"']?"
        r"[\s\S]{0,40}?(?P<args>\{)",
        re.I,
    ),
    re.compile(
        r"(?:^|\n)\s*(?:tool|function)\s*[：:]\s*"
        r"[`\"']?(?P<name>[A-Za-z_][\w.-]{1,64})[`\"']?"
        r"\s*(?P<args>\{)",
        re.I,
    ),
    re.compile(
        r"\{\s*\"(?:tool|name|function)\"\s*:\s*\"(?P<name>[A-Za-z_][\w.-]{1,64})\""
        r"[\s\S]{0,200}?\"(?:arguments|parameters|input)\"\s*:\s*(?P<args>\{)",
        re.I,
    ),
)

_LEAK_HINT = re.compile(
    r"(?i)(<tool_call>|</tool_call>|invoke\s+tool|"
    r"\"name\"\s*:\s*\"(?:manage_mcp|update_config|configure_tevarn|use_tool_pack)\"|"
    r"function\s*call|tool_calls\s*\[|```(?:json|tool|tool_call)[\s\S]{0,80}\"name\")"
)


def _extract_balanced_json_object(s: str, start: int = 0) -> str | None:
    """从 start 起找第一个 {…}（字符串感知、深度计数）。"""
    i = s.find("{", start)
    if i < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(s)):
        ch = s[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[i : j + 1]
    return None


def _parse_args(raw: str) -> dict[str, Any] | None:
    """成功返回 dict；失败返回 None（不可回收）。"""
    s = (raw or "").strip()
    if not s:
        return {}
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {"value": obj}
    except Exception:
        pass
    blob = _extract_balanced_json_object(s)
    if blob:
        try:
            obj = json.loads(blob)
            return obj if isinstance(obj, dict) else {"value": obj}
        except Exception:
            pass
        try:
            obj, _ = json.JSONDecoder().raw_decode(blob)
            return obj if isinstance(obj, dict) else {"value": obj}
        except Exception:
            pass
    return None


def extract_pseudo_tool_calls(
    content: str,
    *,
    whitelist: frozenset[str] | None = None,
) -> list[tuple[str, dict[str, Any], str]]:
    """返回 [(name, args, matched_span), ...]。按文档顺序；解析失败跳过。"""
    if not content or not content.strip():
        return []
    allow = whitelist if whitelist is not None else RECOVER_WHITELIST
    candidates: list[tuple[int, str, dict[str, Any], str]] = []
    for pat in _PATTERNS:
        for m in pat.finditer(content):
            name = (m.groupdict().get("name") or "").strip()
            if not name or name not in allow:
                continue
            args_g = m.groupdict().get("args")
            if args_g is None:
                continue
            try:
                args_start = m.start("args")
            except Exception:
                args_start = m.start()
            blob = _extract_balanced_json_object(content, args_start)
            args = _parse_args(blob or args_g)
            if args is None:
                continue
            if set(args.keys()) == {"_raw"}:
                continue
            end = args_start + len(blob) if blob else m.end()
            span = content[m.start() : max(end, m.end())]
            candidates.append((m.start(), name, args, span))
    candidates.sort(key=lambda x: x[0])
    found: list[tuple[str, dict[str, Any], str]] = []
    seen: set[str] = set()
    for _, name, args, span in candidates:
        key = f"{name}|{json.dumps(args, sort_keys=True, ensure_ascii=False)[:120]}"
        if key in seen:
            continue
        seen.add(key)
        found.append((name, args, span))
    return found


def looks_like_pseudo_tool_content(content: str) -> bool:
    if not content:
        return False
    return bool(_LEAK_HINT.search(content))


def scrub_leak_markers(content: str) -> str:
    """弱清理：去掉伪 tool 标记，保留可读残句。"""
    if not content:
        return content
    s = content
    s = re.sub(r"(?is)<tool_call>[\s\S]*?</tool_call>", "", s)
    s = re.sub(r"(?is)```(?:json|tool|tool_call)[\s\S]*?```", "", s)
    s = re.sub(r"(?i)</?tool_call>", "", s)
    return s.strip()


def leak_nudge_text(*, streak: int = 1) -> str:
    if streak >= 2:
        return (
            "[伪工具泄漏] 本轮禁止工具，用简短文字说明阻塞点。"
            "不要把 tool_call / JSON 工具写在正文；若需工具请走 native tool_calls。"
        )
    return (
        "[伪工具泄漏] 你把工具调用写进了正文。下一轮请用原生 tool_calls，"
        "不要输出 <tool_call> 或 ```json 工具块。"
    )


def recover_tool_calls_from_content(
    content: str,
    *,
    whitelist: frozenset[str] | None = None,
) -> tuple[list[Any], str]:
    """解析正文伪 tool → ToolCall 列表 + 剥离后的展示正文。"""
    hits = extract_pseudo_tool_calls(content, whitelist=whitelist)
    allow = whitelist if whitelist is not None else RECOVER_WHITELIST
    if not hits:
        for m in re.finditer(
            r"```(?:json|tool|tool_call)?\s*(\{[\s\S]*?\})\s*```", content, re.I
        ):
            blob = _extract_balanced_json_object(m.group(1)) or m.group(1)
            try:
                obj = json.loads(blob)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            name = str(obj.get("name") or obj.get("tool") or "").strip()
            args = obj.get("arguments") or obj.get("parameters") or obj.get("input") or {}
            if name in allow and isinstance(args, dict):
                hits.append((name, args, m.group(0)))
                break
    if not hits:
        return [], content

    try:
        from backend.services.llm.schemas import ToolCall
    except Exception:
        logger.debug("ToolCall import failed", exc_info=True)
        return [], content

    tcs: list[Any] = []
    cleaned = content
    for name, args, span in hits:
        tcs.append(
            ToolCall(
                id=f"pseudo_{uuid.uuid4().hex[:12]}",
                name=name,
                arguments=args if isinstance(args, dict) else {},
            )
        )
        if span and span in cleaned:
            cleaned = cleaned.replace(span, "", 1)
    cleaned = scrub_leak_markers(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return tcs, cleaned


__all__ = [
    "RECOVER_WHITELIST",
    "extract_pseudo_tool_calls",
    "looks_like_pseudo_tool_content",
    "scrub_leak_markers",
    "leak_nudge_text",
    "recover_tool_calls_from_content",
]
