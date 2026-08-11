"""正文伪 toolcall 回收：模型把工具写进 content 时二次解析为 native ToolCall。

策略：
A) 可解析白名单 / schema 安全工具 → 转 ToolCall，正文剥离伪形态
B) 像 tool 但解析失败 → 泄漏计数 + nudge / force_final（禁止当终稿）

含 DeepSeek/DSML 方言：
  <|DSML|tool_calls> … <|DSML|invoke name="mcp_…"> <|DSML|parameter …>
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# 默认仅回收「配置/运维」类低危工具；command/file_write 等永不从正文执行
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
        "result_load",
    }
)

# 本轮 schema 内可额外回收的只读/检索类（仍须 schema 过滤）
SCHEMA_SAFE_EXTRA: frozenset[str] = frozenset(
    {
        "web_search",
        "search",
        "fetch_webpage",
        "http",
        "result_load",
        "session_search",
        "doc_read",
        "file_read",
        "grep",
        "glob",
    }
)

_WRITEISH = re.compile(
    r"(?i)(write|delete|exec|run|shell|command|apply_patch|file_write|edit\b)"
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

# DeepSeek / 部分网关把 tool 写成 DSML 标签进 content
_DSML_INVOKE = re.compile(
    r"""(?:<\|?/?\s*DSML\s*\|?\s*)?invoke\s+name\s*=\s*["'](?P<name>[A-Za-z_][\w.-]{1,64})["']""",
    re.I,
)
_DSML_PARAM = re.compile(
    r"""parameter\s+name\s*=\s*["'](?P<key>[A-Za-z_][\w.-]*)["'][^>]*>\s*(?P<val>[\s\S]*?)\s*</[^>]*parameter""",
    re.I,
)

_LEAK_HINT = re.compile(
    r"(?i)("
    r"<tool_call>|</tool_call>|invoke\s+tool|"
    r"invoke\s+name\s*=|"
    r"DSML\s*[|｜]?\s*tool_calls|DSML\s*[|｜]?\s*invoke|"
    r"<\|?\s*DSML|"
    r"\"name\"\s*:\s*\"(?:manage_mcp|update_config|configure_tevarn|use_tool_pack|mcp_)\"|"
    r"function\s*call|tool_calls\s*\[|"
    r"```(?:json|tool|tool_call)[\s\S]{0,80}\"name\""
    r")"
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


def name_is_recoverable(
    name: str,
    *,
    whitelist: frozenset[str] | set[str] | None = None,
    schema_names: set[str] | frozenset[str] | None = None,
) -> bool:
    """是否允许从正文回收该工具名。

    - 默认白名单：运维/澄清类
    - 若提供 schema_names：仅 schema 内 + (白名单 | mcp_* 只读 | SCHEMA_SAFE_EXTRA)
    - 永不回收明显写/执行类（除非已在默认白名单，白名单本身不含 command）
    """
    n = (name or "").strip()
    if not n:
        return False
    allow = set(whitelist if whitelist is not None else RECOVER_WHITELIST)
    if schema_names is not None:
        if n not in schema_names:
            return False
        if n in allow or n in SCHEMA_SAFE_EXTRA:
            return True
        if n.startswith("mcp_") and not _WRITEISH.search(n):
            return True
        return False
    return n in allow


def _extract_dsml_invokes(
    content: str,
    *,
    allow: set[str],
) -> list[tuple[int, str, dict[str, Any], str]]:
    """解析 DSML/invoke name= 方言。"""
    out: list[tuple[int, str, dict[str, Any], str]] = []
    if not content:
        return out
    matches = list(_DSML_INVOKE.finditer(content))
    for i, m in enumerate(matches):
        name = (m.group("name") or "").strip()
        if not name or name not in allow:
            continue
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        chunk = content[start:end]
        close_m = re.search(
            r"</\s*\|?\s*DSML\s*\|?\s*tool_calls\s*>|"
            r"</\s*\|?\s*DSML\s*\|?\s*invoke\s*>|"
            r"</\s*tool_calls\s*>",
            chunk,
            re.I,
        )
        if close_m:
            chunk = chunk[: close_m.end()]
            end = start + close_m.end()
        args: dict[str, Any] = {}
        for pm in _DSML_PARAM.finditer(chunk):
            k = (pm.group("key") or "").strip()
            v = (pm.group("val") or "").strip()
            if not k:
                continue
            if re.fullmatch(r"-?\d+", v):
                try:
                    args[k] = int(v)
                    continue
                except Exception:
                    pass
            if v.lower() in ("true", "false"):
                args[k] = v.lower() == "true"
                continue
            args[k] = v
        if not args:
            blob = _extract_balanced_json_object(chunk, 0)
            parsed = _parse_args(blob or "")
            if parsed:
                args = parsed
        span = content[start:end]
        out.append((start, name, args, span))
    return out


def extract_pseudo_tool_calls(
    content: str,
    *,
    whitelist: frozenset[str] | set[str] | None = None,
    schema_names: set[str] | frozenset[str] | None = None,
) -> list[tuple[str, dict[str, Any], str]]:
    """返回 [(name, args, matched_span), ...]。按文档顺序；解析失败跳过。"""
    if not content or not content.strip():
        return []
    base_wl = set(whitelist if whitelist is not None else RECOVER_WHITELIST)
    if schema_names is not None:
        for n in schema_names:
            if name_is_recoverable(n, whitelist=base_wl, schema_names=set(schema_names)):
                base_wl.add(n)
    allow = base_wl
    candidates: list[tuple[int, str, dict[str, Any], str]] = []
    for pat in _PATTERNS:
        for m in pat.finditer(content):
            name = (m.groupdict().get("name") or "").strip()
            if not name or not name_is_recoverable(
                name, whitelist=allow, schema_names=schema_names
            ):
                continue
            if name not in allow and schema_names is None:
                continue
            if schema_names is not None and name not in schema_names:
                continue
            # 统一：必须在 allow（已并入 schema 安全名）
            if name not in allow:
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
    # DSML
    for start, name, args, span in _extract_dsml_invokes(content, allow=allow):
        if schema_names is not None and name not in schema_names:
            continue
        if not name_is_recoverable(name, whitelist=allow, schema_names=schema_names):
            continue
        candidates.append((start, name, args, span))
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
    # DSML blocks
    s = re.sub(
        r"(?is)<[^>]{0,40}DSML[^>]{0,40}tool_calls[^>]*>[\s\S]*?"
        r"(?:</[^>]{0,40}DSML[^>]{0,80}>|$)",
        "",
        s,
    )
    s = re.sub(
        r"(?is)invoke\s+name\s*=\s*[\"'][^\"']+[\"'][\s\S]{0,4000}?"
        r"(?=invoke\s+name\s*=|<[^>]*DSML|</\s*\|?\s*DSML|$)",
        "",
        s,
    )
    s = re.sub(r"(?i)</?[^>]*DSML[^>]*>", "", s)
    return s.strip()


def leak_nudge_text(*, streak: int = 1) -> str:
    if streak >= 2:
        return (
            "[伪工具泄漏] 本轮请用简短文字给出已有结论；"
            "不要再输出 DSML / <tool_call> / JSON 工具块。"
            "若必须再搜，请使用**原生 tool_calls**（不是正文标签）。"
        )
    return (
        "[伪工具泄漏] 你把工具调用写进了正文（如 DSML invoke / <tool_call>）。"
        "下一轮请用**原生 function/tool_calls**；"
        "若上一轮有 [tool_result_handle id=…]，请先 result_load 分页再总结，"
        "勿重复同一搜索。"
    )


def recover_tool_calls_from_content(
    content: str,
    *,
    whitelist: frozenset[str] | set[str] | None = None,
    schema_names: set[str] | frozenset[str] | None = None,
) -> tuple[list[Any], str]:
    """解析正文伪 tool → ToolCall 列表 + 剥离后的展示正文。"""
    hits = extract_pseudo_tool_calls(
        content, whitelist=whitelist, schema_names=schema_names
    )
    base_wl = set(whitelist if whitelist is not None else RECOVER_WHITELIST)
    if schema_names is not None:
        for n in schema_names:
            if name_is_recoverable(n, whitelist=base_wl, schema_names=set(schema_names)):
                base_wl.add(n)
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
            if (
                name_is_recoverable(
                    name, whitelist=base_wl, schema_names=schema_names
                )
                and isinstance(args, dict)
            ):
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
    "SCHEMA_SAFE_EXTRA",
    "name_is_recoverable",
    "extract_pseudo_tool_calls",
    "looks_like_pseudo_tool_content",
    "scrub_leak_markers",
    "leak_nudge_text",
    "recover_tool_calls_from_content",
]
