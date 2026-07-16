"""Propose skill/tool improvements — HAEE-style structured generators (v0.1.1)."""

from __future__ import annotations

import hashlib
import re
from typing import Any


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
        if "no column named" in res.lower() or "operationalerror" in res.lower():
            codes.append("schema_mismatch")
    text = final_content or ""
    if "答案1" in text or "答案 1" in text or text.count("\n## ") >= 3:
        codes.append("multi_answer_dump")
    if "不知道" in text and len(text) < 40:
        codes.append("low_confidence")
    # no verification tools after mutations
    names = [str(t.get("name") or "") for t in (tool_trace or [])]
    mut = any(n in {"write_file", "patch", "terminal", "execute_code"} for n in names)
    ver = any(n in {"read_file", "terminal", "run_tests"} for n in names[-3:]) if names else False
    if mut and not ver and len(names) >= 2:
        codes.append("missing_verification")
    seen: set[str] = set()
    out: list[str] = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _slug(text: str, prefix: str = "evo") -> str:
    raw = (text or "task").strip().lower()
    raw = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")[:28] or "task"
    # keep ascii-ish name for tools
    ascii_part = re.sub(r"[^a-z0-9_]+", "", raw) or hashlib.md5(text.encode()).hexdigest()[:8]
    return f"{prefix}_{ascii_part}"[:64]


def propose_skill_from_failure(
    *,
    user_input: str,
    failure_codes: list[str],
    tool_trace: list[dict[str, Any]] | None = None,
    final_content: str = "",
    source_label: str = "turn",
) -> dict[str, Any]:
    """Full SKILL.md-style playbook (P2)."""
    tools = tool_trace or []
    tool_names = [t.get("name") or "?" for t in tools][:16]
    codes = ", ".join(failure_codes) or "unknown"
    name = _slug(user_input or source_label, "evo")
    summary = f"[{source_label}] 针对「{(user_input or '')[:48]}」的可复用流程（{codes}）"

    # pick generator flavor
    if "missing_verification" in failure_codes:
        body = _gen_verification_skill(name, user_input, tool_names, final_content)
    elif "schema_mismatch" in failure_codes or "tool_error" in failure_codes:
        body = _gen_troubleshooting_skill(name, user_input, tool_names, codes, final_content)
    elif "tool_timeout" in failure_codes:
        body = _gen_time_efficient_skill(name, user_input, tool_names)
    else:
        body = _gen_general_skill(name, user_input, tool_names, codes, final_content)

    return {
        "kind": "skill",
        "name": name,
        "summary": summary[:500],
        "content": body,
        "meta": {
            "failure_codes": failure_codes,
            "tools": tool_names,
            "source_label": source_label,
            "format": "skill_md_v011",
        },
    }


def propose_tool_draft(
    *,
    user_input: str,
    failure_codes: list[str],
    tool_trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """P3: playbook-style tool draft (not arbitrary code)."""
    tools = tool_trace or []
    tool_names = [t.get("name") or "?" for t in tools][:12]
    name = _slug(user_input or "helper", "tool")
    summary = f"进化工具草稿：辅助处理「{(user_input or '')[:40]}」"
    content = f"""# Tool Playbook: {name}

## When to use
- User intent ≈ {(user_input or '').strip()[:180]}
- Failure codes: {', '.join(failure_codes) or 'n/a'}

## Parameters
- query (string): 用户问题或上下文摘要

## Steps (handler is playbook injection, not shell)
1. Read query and map to known tools: {', '.join(tool_names) if tool_names else '(discover)'}.
2. Prefer existing builtins over inventing new side effects.
3. Validate outputs; never invent API results.
4. Return one structured summary in Chinese, conclusion first.

## Safety
- Do not print secrets/tokens.
- Do not run destructive shell without confirmation.
- Status: **draft** until user enables in 自主进化.

## Pitfalls
- Schema mismatch: align UI fields with DB columns before INSERT.
- Empty tool results: retry once with narrower args then report.
"""
    return {
        "kind": "tool",
        "name": name,
        "summary": summary[:500],
        "content": content,
        "meta": {
            "failure_codes": failure_codes,
            "tools": tool_names,
            "format": "tool_playbook_v011",
            "executable": False,
        },
    }


def propose_from_task_outcome(
    *,
    task_name: str,
    success: bool,
    detail: str = "",
    failure_codes: list[str] | None = None,
    tool_trace: list[dict[str, Any]] | None = None,
    criteria_summary: str = "",
) -> dict[str, Any]:
    """P1: after cron/task run, propose a skill capturing the outcome."""
    codes = list(failure_codes or ([] if success else ["task_failed"]))
    label = f"task:{task_name}"
    user_input = f"任务 {task_name} " + ("成功复盘" if success else "失败复盘")
    if criteria_summary:
        user_input += f" — {criteria_summary[:80]}"
    skill = propose_skill_from_failure(
        user_input=user_input,
        failure_codes=codes or (["task_ok"] if success else ["task_failed"]),
        tool_trace=tool_trace,
        final_content=detail[:1200],
        source_label=label,
    )
    skill["meta"]["task_name"] = task_name
    skill["meta"]["task_success"] = success
    return skill


def text_similarity(a: str, b: str) -> float:
    """Simple token Jaccard for dedupe."""
    def toks(s: str) -> set[str]:
        s = (s or "").lower()
        parts = re.findall(r"[a-z0-9_\u4e00-\u9fff]{2,}", s)
        return set(parts)
    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta | tb), 1)


def _gen_verification_skill(name, user_input, tool_names, final_content) -> str:
    return f"""---
name: {name}
description: "完成变更后必须验证再宣告完成。用于写文件/改配置/跑命令后的验收。"
version: "0.1.1"
---

# {name}

## When to use
- 刚修改了文件、配置或执行了可能有副作用的操作
- 用户意图：{(user_input or '')[:200]}

## Steps
1. 列出本次改动的路径/接口。
2. 用 `read_file` / 健康检查 / 相关命令复验（不要只靠记忆）。
3. 确认 exit code / HTTP 200 / 关键字符串存在。
4. 再向用户报告「已完成」；失败则给重试步骤。

## Tools often involved
{', '.join(tool_names) if tool_names else 'read_file, terminal'}

## Pitfalls
- Agent forgot to check exit codes after shell commands.
- Declaring done when only partial files were written.
- Skipping tests because the happy path looked fine.

## Verification checklist
- [ ] 关键文件存在且内容匹配
- [ ] 相关服务 health 正常
- [ ] 无未处理的 tool error

## Reference snippet
{(final_content or '')[:600]}
"""


def _gen_troubleshooting_skill(name, user_input, tool_names, codes, final_content) -> str:
    return f"""---
name: {name}
description: "排查工具失败与 schema/环境错误的可复用流程。"
version: "0.1.1"
---

# {name}

## When to use
- 工具返回 Error / not found / schema mismatch
- 用户意图：{(user_input or '')[:200]}
- 失败码：{codes}

## Steps
1. 复现：用同一参数再调一次，确认非瞬时故障。
2. 对齐契约：查模型/API 字段与 DB 列是否一致（UI `command` vs DB `workflow_id` 类问题）。
3. 缩小范围：换最小参数集；检查权限与路径。
4. 修复后写验证步骤；沉淀到 skill Pitfalls。

## Tools
{', '.join(tool_names) if tool_names else '(context-dependent)'}

## Pitfalls
- Inserting columns that do not exist.
- Ignoring encrypted/masked secrets when re-fetching models.
- Treating 200 API create as success when payload fields were stripped.

## Reference
{(final_content or '')[:600]}
"""


def _gen_time_efficient_skill(name, user_input, tool_names) -> str:
    return f"""---
name: {name}
description: "超时与长任务的优先级与分批策略。"
version: "0.1.1"
---

# {name}

## When to use
- 工具超时、任务过长
- 用户意图：{(user_input or '')[:200]}

## Steps
1. 先做 30 秒内可完成的诊断。
2. 长构建/下载改 background + 轮询。
3. 并行互不依赖的 IO。
4. 给用户中期进度，避免静默。

## Tools
{', '.join(tool_names) if tool_names else 'terminal, process'}

## Pitfalls
- Blocking on multi-minute npm builds in the foreground without notify.
"""


def _gen_general_skill(name, user_input, tool_names, codes, final_content) -> str:
    return f"""---
name: {name}
description: "可复用处理流程：{(user_input or 'general task')[:80]}"
version: "0.1.1"
---

# {name}

## When to use
- 用户意图接近：{(user_input or '').strip()[:220]}
- 失败/信号码：{codes}

## Steps
1. 先收集事实（工具），禁止空口编造。
2. 合并多工具结果为**一份**答复，结论先行。
3. 失败则说明原因 + 可重试动作。
4. 完成后做最小验证再收尾。

## Tools often useful
{', '.join(tool_names) if tool_names else 'discover from registry'}

## Pitfalls
- Multi-answer dumps (答案1/2/3).
- Leaking secrets into skills or logs.
- Skipping verification after mutations.

## Reference draft (truncated)
{(final_content or '')[:700]}
"""
