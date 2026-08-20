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
    r"<arg_key>|<arg_value>|"
    r"[A-Za-z_][\w.-]{0,64}<arg_(?:key|value)>|"
    r"\"name\"\s*:\s*\"(?:manage_mcp|update_config|configure_tevarn|use_tool_pack|result_load|mcp_)\"|"
    r"function\s*call|tool_calls\s*\[|"
    r"```(?:json|tool|tool_call)[\s\S]{0,80}\"name\"|"
    # grok 等把 tool 写成 { "name": "…" } 后死循环输出 }
    r"\}(?:[ \t]*\r?\n?[ \t]*\}){6,}|"
    # xAI / Grok Harmony 方言：<|tool_call_begin|> … 写进正文
    r"<\|tool_calls_section_begin\|>|<\|tool_call_begin\|>|"
    # 另一套 Grok 方言：<|uniquecall_id|>5</uniquecall_id>result_load<|uniqueid|>…
    r"<\|uniquecall_id\|>|<\|unique[a-z_]+\|>|</uniquecall>"
    r")"
)

# { "name": "result_load" … } 伪 tool 对象（arguments 可缺）
_NAME_JSON_OPEN = re.compile(
    r"\{\s*\"(?:name|tool|function)\"\s*:\s*\"(?P<name>[A-Za-z_][\w.-]{1,64})\"",
    re.I,
)
# 文末 } / } \n 复读（至少 4 次）
_BRACE_LOOP_TAIL = re.compile(r"(?:[ \t]*\}[ \t]*\r?\n?){4,}\s*$")


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
    if n in allow:
        return True
    if schema_names is not None:
        if n not in schema_names:
            return False
        if n in SCHEMA_SAFE_EXTRA:
            return True
        if n.startswith("mcp_") and not _WRITEISH.search(n):
            return True
        return False
    return False


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


_GROK_CALL = re.compile(
    r"<\|tool_call_begin\|>([\s\S]*?)<\|tool_call_end\|>",
    re.I,
)
_UNIQUE_CALL_OPEN = re.compile(
    r"<\|uniquecall_id\|>\d+</uniquecall_id>",
    re.I,
)
_UNIQUE_ARG = re.compile(
    r"<\|unique[A-Za-z_][\w]*\|>(?P<key>[A-Za-z_][\w]*)"
    r"(?:</unique[A-Za-z_][\w]*>)?"
    r"(?P<val>[\s\S]*?)"
    r"(?=<\|unique|</uniquecall>|$)",
    re.I,
)
_UNIQUE_BLOCK = re.compile(
    r"(?is)<\|uniquecall_id\|>[\s\S]*?(?:</uniquecall>|$)",
)
_UNIQUE_TAG = re.compile(
    r"</?unique[A-Za-z_][\w]*>|<\|unique[A-Za-z_][\w]*\|>",
    re.I,
)


def _coerce_grok_arg(raw: str) -> Any:
    s = (raw or "").strip()
    if re.fullmatch(r"-?\d+", s):
        try:
            return int(s)
        except Exception:
            return s
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    return s


def _extract_grok_invokes(
    content: str,
    *,
    allow: set[str],
) -> list[tuple[int, str, dict[str, Any], str]]:
    """解析 Grok / xAI 写进正文的 <|tool_call_begin|> 方言。"""
    out: list[tuple[int, str, dict[str, Any], str]] = []
    if not content or "<|tool_call" not in content:
        return out
    for m in _GROK_CALL.finditer(content):
        raw = m.group(1) or ""
        raw = re.sub(r"<\|tool_call_begin\|>", "", raw)
        raw = re.sub(r"<\|tool_call_argument_begin\|>", "\n", raw)
        raw = raw.strip()
        name = ""
        args: dict[str, Any] = {}
        hm = re.search(
            r"(?:functions\.)?(?P<name>[A-Za-z_][\w.-]{1,64})(?::\d+)?\s*"
            r"(?P<json>\{[\s\S]*\})?",
            raw,
        )
        if hm and "{" in (hm.group("json") or ""):
            name = (hm.group("name") or "").strip()
            parsed = _parse_args(hm.group("json") or "")
            if parsed:
                args = parsed
        if not name:
            lines = [
                ln.strip()
                for ln in raw.splitlines()
                if ln.strip() and not ln.strip().startswith("<|")
            ]
            if not lines:
                continue
            name = lines[0]
            i = 1
            while i < len(lines):
                k = lines[i]
                if i + 1 < len(lines) and re.fullmatch(r"[A-Za-z_][\w.-]*", k):
                    args[k] = _coerce_grok_arg(lines[i + 1])
                    i += 2
                else:
                    i += 1
        if not name or name not in allow:
            continue
        out.append((m.start(), name, args, m.group(0)))
    return out


def _coerce_unique_arg(raw: str) -> Any:
    s = (raw or "").strip()
    if not s:
        return s
    if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
        try:
            return json.loads(s)
        except Exception:
            pass
    return _coerce_grok_arg(s)


def _extract_unique_invokes(
    content: str,
    *,
    allow: set[str],
) -> list[tuple[int, str, dict[str, Any], str]]:
    """解析 Grok 写进正文的 <|uniquecall_id|>…</uniquecall> 方言。"""
    out: list[tuple[int, str, dict[str, Any], str]] = []
    if not content or "<|unique" not in content:
        return out
    opens = list(_UNIQUE_CALL_OPEN.finditer(content))
    if not opens:
        return out
    for i, m in enumerate(opens):
        start = m.start()
        end = opens[i + 1].start() if i + 1 < len(opens) else len(content)
        chunk = content[m.end() : end]
        close = re.search(r"</uniquecall>?", chunk, flags=re.I)
        if close:
            span_end = m.end() + close.end()
            chunk = chunk[: close.start()]
        else:
            span_end = end
        nm = re.match(r"\s*([A-Za-z_][\w.-]{1,64})", chunk)
        if not nm:
            continue
        name = nm.group(1)
        if name not in allow:
            continue
        args: dict[str, Any] = {}
        for am in _UNIQUE_ARG.finditer(chunk[nm.end() :]):
            key = (am.group("key") or "").strip()
            if not key:
                continue
            args[key] = _coerce_unique_arg(am.group("val") or "")
        out.append((start, name, args, content[start:span_end]))
    # One leak dump often mixes paging + a search flood. Keep result_load;
    # extra search/extract from the same blob is what the user just cancelled.
    if any(n == "result_load" for _, n, _, _ in out):
        out = [item for item in out if item[1] == "result_load"]
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
            if (
                schema_names is not None
                and name not in schema_names
                and name not in allow
            ):
                continue
            # 统一：必须在 allow（已并入 schema 安全名 / 默认白名单）
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
        if (
            schema_names is not None
            and name not in schema_names
            and name not in allow
        ):
            continue
        if not name_is_recoverable(name, whitelist=allow, schema_names=schema_names):
            continue
        candidates.append((start, name, args, span))
    for start, name, args, span in _extract_grok_invokes(content, allow=allow):
        if (
            schema_names is not None
            and name not in schema_names
            and name not in allow
        ):
            continue
        if not name_is_recoverable(name, whitelist=allow, schema_names=schema_names):
            continue
        candidates.append((start, name, args, span))
    for start, name, args, span in _extract_unique_invokes(content, allow=allow):
        if (
            schema_names is not None
            and name not in schema_names
            and name not in allow
        ):
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
    return bool(_LEAK_HINT.search(content)) or bool(_BRACE_LOOP_TAIL.search(content))


def _norm_repeat_line(line: str) -> str:
    return re.sub(r"[`*_]+", "", (line or "")).strip()


def _similar_repeat_line(a: str, b: str) -> bool:
    """True when two tail lines are the same progress sentence with small drift."""
    x, y = _norm_repeat_line(a), _norm_repeat_line(b)
    if not x or not y:
        return False
    if x == y:
        return True
    shorter, longer = (x, y) if len(x) <= len(y) else (y, x)
    if len(shorter) >= 12 and (longer.startswith(shorter) or longer.endswith(shorter)):
        return True
    n = 0
    for ca, cb in zip(x, y):
        if ca != cb:
            break
        n += 1
    n_suf = 0
    for ca, cb in zip(reversed(x), reversed(y)):
        if ca != cb:
            break
        n_suf += 1
    return n >= 16 or n_suf >= 16


def _tail_similar_line_run(text: str) -> int:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return 0
    last = lines[-1]
    run = 1
    for prev in reversed(lines[:-1]):
        if _similar_repeat_line(prev, last):
            run += 1
        else:
            break
    return run


def _blocks_similar(a: list[str], b: list[str]) -> bool:
    if len(a) != len(b) or not a:
        return False
    hits = sum(1 for x, y in zip(a, b) if _similar_repeat_line(x, y))
    need = max(1, (len(a) + 1) // 2)
    return hits >= need


def _tail_block_run(text: str) -> tuple[int, int]:
    """Return (run, block_len) of similar line-blocks at the tail."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    n = len(lines)
    if n < 6:
        return 0, 0
    best_run, best_len = 0, 0
    max_block = min(12, n // 3)
    for block_len in range(1, max_block + 1):
        run = 1
        pos = n
        while pos - block_len * 2 >= 0:
            later = lines[pos - block_len : pos]
            earlier = lines[pos - 2 * block_len : pos - block_len]
            if _blocks_similar(earlier, later):
                run += 1
                pos -= block_len
            else:
                break
        if run > best_run or (run == best_run and block_len > best_len):
            best_run, best_len = run, block_len
    return best_run, best_len


def _hot_repeat_count(text: str, *, tail_lines: int = 80) -> int:
    """How often the most-repeated long line (with drift) appears."""
    lines = [
        ln for ln in (text or "").splitlines() if len((ln or "").strip()) >= 20
    ]
    if tail_lines and len(lines) > tail_lines:
        lines = lines[-tail_lines:]
    if len(lines) < 4:
        return 0
    best = 1
    for i, a in enumerate(lines):
        n = 1
        for b in lines[i + 1 :]:
            if _similar_repeat_line(a, b):
                n += 1
        if n > best:
            best = n
        if best >= 4:
            return best
    return best


def _collapse_tail_blocks(s: str) -> str:
    lines = s.splitlines()
    nonempty = [(i, ln) for i, ln in enumerate(lines) if ln.strip()]
    n = [ln for _, ln in nonempty]
    if len(n) < 6:
        return s
    best_removed, keep_n, best_len = 0, 0, 0
    max_block = min(12, len(n) // 3)
    for block_len in range(max_block, 0, -1):
        run = 1
        pos = len(n)
        while pos - block_len * 2 >= 0:
            later = n[pos - block_len : pos]
            earlier = n[pos - 2 * block_len : pos - block_len]
            if _blocks_similar(earlier, later):
                run += 1
                pos -= block_len
            else:
                break
        if run >= 3:
            removed = block_len * (run - 1)
            if removed > best_removed or (
                removed == best_removed and block_len > best_len
            ):
                best_removed = removed
                keep_n = len(n) - removed
                best_len = block_len
    if best_removed < 4 or keep_n <= 0:
        return s
    count = 0
    cut_i = 0
    for i, ln in enumerate(lines):
        if ln.strip():
            count += 1
            if count >= keep_n:
                cut_i = i + 1
                break
    return "\n".join(lines[:cut_i]).rstrip()


def _collapse_similar_line_copies(
    s: str, *, keep: int = 1, min_skip: int = 3
) -> str:
    """Keep the first copy of each similar long line; drop later loop copies."""
    lines = s.splitlines()
    kept_canon: list[str] = []
    out: list[str] = []
    skipped = 0
    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            if out and out[-1].strip():
                out.append(ln)
            continue
        if len(stripped) < 12:
            out.append(ln)
            continue
        hits = sum(1 for c in kept_canon if _similar_repeat_line(c, stripped))
        if hits >= keep:
            skipped += 1
            continue
        kept_canon.append(_norm_repeat_line(stripped) or stripped)
        out.append(ln)
    if skipped < min_skip:
        return s
    return "\n".join(out).rstrip()


def _cut_before_second_hot(s: str) -> str:
    """Once a long line family repeats, drop everything from the 2nd copy.

    Drifting planning loops (A,B,C,A',B',C') do not share a tail line, but
    they do reuse the same anchor sentence. Keep the first cycle only.
    """
    lines = s.splitlines()
    items = [(i, ln) for i, ln in enumerate(lines) if len((ln or "").strip()) >= 20]
    if len(items) < 4:
        return s
    best_n, best_ln = 0, ""
    for i, (_, a) in enumerate(items):
        n = 1
        for _, b in items[i + 1 :]:
            if _similar_repeat_line(a, b):
                n += 1
        if n > best_n:
            best_n, best_ln = n, a
    if best_n < 4 or not best_ln:
        return s
    seen = 0
    cut = None
    for i, ln in items:
        if _similar_repeat_line(best_ln, ln):
            seen += 1
            if seen == 2:
                cut = i
                break
    if cut is None or cut < 1:
        return s
    return "\n".join(lines[:cut]).rstrip()


def looks_like_token_loop(accumulated: str, delta: str = "") -> bool:
    """流式尾部是否在复读：}\\n 短 token、同一进度句连刷、或整段规划循环。"""
    tail = (accumulated or "")[-800:]
    if not tail:
        return False
    if _BRACE_LOOP_TAIL.search(tail) and tail.count("}") >= 8:
        return True
    d = delta or ""
    if d and re.fullmatch(r"[\s}\]]+", d) and tail.count("}") >= 8:
        return True
    if _tail_similar_line_run(accumulated) >= 4:
        return True
    run, block_len = _tail_block_run(accumulated)
    if run >= 3 and block_len >= 2:
        return True
    if _hot_repeat_count(accumulated) >= 4:
        return True
    collapsed = collapse_repetition_tail(accumulated)
    if len(accumulated) >= 120 and len(collapsed) + 40 < len(accumulated) * 0.6:
        return True
    return False


def collapse_repetition_tail(content: str) -> str:
    """去掉文末同一短 token / 同一进度句 / 整段循环，保留一句。"""
    if not content:
        return content
    s = _BRACE_LOOP_TAIL.sub("", content)
    for n in (1, 2, 3, 4):
        if len(s) < n * 8:
            continue
        unit = s[-n:]
        if not unit.strip() or (n >= 2 and unit.strip().isalnum()):
            continue
        run = 0
        i = len(s)
        while i >= n and s[i - n : i] == unit:
            run += 1
            i -= n
        if run >= 8:
            s = s[: i + n]
            break
    s = re.sub(r"(?:\n[ \t]*)+\}[ \t]*\s*$", "", s)
    lines = s.splitlines()
    nonempty = [(i, ln) for i, ln in enumerate(lines) if ln.strip()]
    if len(nonempty) >= 3:
        _idx, last = nonempty[-1]
        run_idx = [nonempty[-1][0]]
        for i, ln in reversed(nonempty[:-1]):
            if _similar_repeat_line(ln, last):
                run_idx.append(i)
            else:
                break
        if len(run_idx) >= 3:
            keep = min(run_idx)
            s = "\n".join(lines[: keep + 1]).rstrip()
    s = _collapse_tail_blocks(s)
    if _hot_repeat_count(s, tail_lines=0) >= 4:
        s = _cut_before_second_hot(s)
        s = _collapse_similar_line_copies(s, min_skip=1)
    return s.rstrip()


def _strip_name_json_leaks(s: str) -> str:
    """砍掉正文里 {\"name\":\"tool\"…} 伪对象；解析失败则从该 { 起到文末丢掉。"""
    m = _NAME_JSON_OPEN.search(s)
    while m:
        blob = _extract_balanced_json_object(s, m.start())
        if blob is None:
            return s[: m.start()].rstrip()
        drop = False
        try:
            obj = json.loads(blob)
            if isinstance(obj, dict) and (
                obj.get("name") or obj.get("tool") or obj.get("function")
            ):
                drop = True
        except Exception:
            drop = True
        if drop:
            s = (s[: m.start()] + s[m.start() + len(blob) :]).rstrip()
            m = _NAME_JSON_OPEN.search(s)
            continue
        m = _NAME_JSON_OPEN.search(s, m.start() + 1)
    return s


def scrub_leak_markers(content: str) -> str:
    """弱清理：去掉伪 tool 标记，保留可读残句。"""
    if not content:
        return content
    s = content
    s = re.sub(r"(?is)<tool_call>[\s\S]*?</tool_call>", "", s)
    s = re.sub(r"(?is)```(?:json|tool|tool_call)[\s\S]*?```", "", s)
    s = re.sub(r"(?i)</?tool_call>", "", s)
    s = re.sub(
        r"(?is)<\|tool_calls_section_begin\|>[\s\S]*?"
        r"(?:<\|tool_calls_section_end\|>|$)",
        "",
        s,
    )
    s = re.sub(
        r"(?is)<\|tool_call_begin\|>[\s\S]*?(?:<\|tool_call_end\|>|$)",
        "",
        s,
    )
    s = re.sub(
        r"(?i)<\|tool_call_(?:begin|end|argument_begin)\|>|"
        r"<\|tool_calls_section_(?:begin|end)\|>",
        "",
        s,
    )
    s = _UNIQUE_BLOCK.sub("", s)
    s = _UNIQUE_TAG.sub("", s)
    s = re.sub(r"(?i)</uniquecall>?", "", s)
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
    # Hermes / MiniMax: command<arg_key>command<arg_value>...
    cut = re.search(
        r"(?is)(?:[A-Za-z_][\w.-]{1,64})?<arg_(?:key|value)>",
        s,
    )
    if cut:
        s = s[: cut.start()]
    s = _strip_name_json_leaks(s)
    s = collapse_repetition_tail(s)
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


def leak_stop_final_text(content: str) -> str:
    """连续伪 tool 泄漏后的交卷正文：有擦除后的可见字就用，否则短停。"""
    text = scrub_leak_markers(content or "").strip()
    if text:
        return text
    return (
        "工具结果已经拿到（见上方预览），但模型把调用写进了正文，这一轮先停。"
        "直接说「继续」即可用已有结果总结。"
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
    "leak_stop_final_text",
    "looks_like_token_loop",
    "collapse_repetition_tail",
    "scrub_leak_markers",
    "leak_nudge_text",
    "recover_tool_calls_from_content",
]
