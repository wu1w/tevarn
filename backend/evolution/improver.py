"""Propose skill improvements from failure context (template-based MVP)."""

from __future__ import annotations

from typing import Any


def propose_skill_from_failure(
    *,
    user_input: str,
    failure_codes: list[str],
    tool_trace: list[dict[str, Any]] | None = None,
    final_content: str = "",
) -> dict[str, Any]:
    """Return proposed skill asset fields (name, summary, content)."""
    tools = tool_trace or []
    tool_names = [t.get("name") or "?" for t in tools][:12]
    codes = ", ".join(failure_codes) or "unknown"

    # Derive a stable-ish slug
    base = "learned"
    if "weather" in (user_input or "").lower() or "天气" in (user_input or ""):
        base = "weather_reply"
    elif any(x in (user_input or "") for x in ("设备", "device", "配对")):
        base = "device_ops"
    elif any(x in (user_input or "") for x in ("配置", "设置", "API")):
        base = "configure_hint"
    else:
        # hash-ish from first 24 chars
        slug = "".join(ch if ch.isalnum() else "_" for ch in (user_input or "task")[:24])
        base = f"learned_{slug.strip('_')[:20] or 'task'}"

    name = f"evo_{base}"[:64]
    summary = f"自主归纳：针对「{(user_input or '')[:40]}」的处理要点（失败码: {codes}）"

    content = f"""# {name}

## 适用
用户意图接近：{(user_input or '').strip()[:200]}

## 失败归因
- codes: {codes}
- tools: {', '.join(tool_names) if tool_names else '(none)'}

## 推荐步骤
1. 先调用合适工具收集事实，不要空口编造。
2. 多工具结果合并为**一份**答复，禁止答案1/2/3并列。
3. 若工具失败：说明失败原因并给出可重试动作。
4. 不要把密钥、token、内网地址写进 skill 或回复日志。

## 答复风格
- 结论先行，短段落。
- 需要远程时用 @设备名，先确认设备在线。

## 参考草稿（来自当轮输出，已截断）
{(final_content or '')[:800]}
"""
    return {
        "kind": "skill",
        "name": name,
        "summary": summary,
        "content": content,
        "meta": {"failure_codes": failure_codes, "tools": tool_names},
    }


def classify_failures(
    *,
    tool_trace: list[dict[str, Any]] | None,
    final_content: str,
    eval_failures: list[str] | None = None,
) -> list[str]:
    codes: list[str] = list(eval_failures or [])
    for t in tool_trace or []:
        res = str(t.get("result") or "")
        name = t.get("name") or ""
        if res.startswith("[Error]") or "not found" in res.lower():
            codes.append("tool_error")
        if "timeout" in res.lower():
            codes.append("tool_timeout")
        if name and res.strip() in {"", "[]", "{}", "null"}:
            codes.append("empty_result")
    text = final_content or ""
    if "答案1" in text or "答案 1" in text or text.count("\n## ") >= 3:
        codes.append("multi_answer_dump")
    if "不知道" in text and len(text) < 40:
        codes.append("low_confidence")
    # dedupe
    seen: set[str] = set()
    out: list[str] = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out
