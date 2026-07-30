"""Dispatch / assign grounding — stop CEO from poisoning workers with hallucinations.

Multi-class hallucination checks on instruction text *before* enqueue:

| Class              | Examples                                      | Default severity |
|--------------------|-----------------------------------------------|------------------|
| phantom paths      | `backend/agent/orchestrator.py` missing       | block            |
| template modules   | session_manager.py / tool_execution.py        | block            |
| hard metrics       | 「必须达到 95%」「转化率=37%」as facts         | block            |
| hard money/counts  | 「营收 1200 万」「共 847 个漏洞」as facts      | block            |
| multi CVE no verify| several CVE-IDs without 核实/verify           | block            |
| stack traces       | Traceback / File "...", line N in assign      | block            |
| fake commit hashes | 40-char sha as established fix                | warn→block if +certain |
| invented APIs      | concrete /api/vN/... routes as facts          | warn             |
| version pins       | pkg==1.2.3 / vX.Y.Z as "current" without note | warn             |
| latest/realtime    | 最新/今天/breaking without worker verify room | warn (block if +certain) |
| absolute certainty | 一定是/根因就是/毫无疑问                      | warn (block if +metric) |
| gift-wrap done     | 已修复/已确认完成 as instruction              | warn             |
| source attribution | 「文档写到」without path                      | warn             |

Returns structured risk so crew_steward can hard-reject or warn.
Workers still re-verify via hygiene block — ticket text is never ground truth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.agent.task_grounding import (
    extract_cited_paths,
    project_roots,
)

# ── known monorepo phantoms from second-round self-audits ─────
_PHANTOM_BASENAMES = frozenset(
    {
        "orchestrator.py",
        "session_manager.py",
        "tool_execution.py",
        "base_service.py",
        "session_state.py",
        "memory_manager.py",
        "agent_manager.py",
        "context_manager.py",
        "workflow_engine.py",
        "task_scheduler.py",
        "permission_manager.py",
        "auth_middleware.py",
    }
)
_PHANTOM_FRAGMENTS = (
    "memory/indexer.py",
    "state/session_state.py",
    "services/memory.py",
    "services/base_service.py",
    "agent/orchestrator.py",
    "agent/session_manager.py",
    "agent/tool_execution.py",
    "core/orchestrator.py",
    "runtime/session_manager.py",
)

# ── multi-class patterns ──────────────────────────────────────
_HARD_NUM = re.compile(
    r"(必须|务必|要求|目标|达到|超过|不少于|至少|保证)\s*"
    r"(\d{1,3}(?:\.\d+)?)\s*%|"
    r"(准确率|召回率|覆盖率|转化率|完成率|通过率|成功率|失败率|错误率|"
    r"accuracy|recall|precision|coverage|conversion)\s*"
    r"(?:为|是|=|：|:|达到|已达)?\s*"
    r"(\d{1,3}(?:\.\d+)?)\s*%",
    re.I,
)
_HARD_COUNT = re.compile(
    r"(共有|共计|总共|发现了?|存在|含有|包括)\s*"
    r"(\d{2,6})\s*"
    r"(个|处|条|项)?\s*"
    r"(漏洞|缺陷|问题|bug|CVE|高危|Critical|High|文件|模块)|"
    r"(营收|收入|亏损|利润|成本|预算)\s*"
    r"(?:为|是|=|达到|约)?\s*"
    r"(\d+(?:\.\d+)?)\s*"
    r"(万|亿|元|美元|USD|RMB)?",
    re.I,
)
_LATEST = re.compile(
    r"(最新|今天|今日|实时|刚刚发布|breaking|"
    r"as\s+of\s+today|current\s+price|live\s+data|"
    r"当前(价格|汇率|版本|行情))",
    re.I,
)
_CERTAIN = re.compile(
    r"(一定是|肯定是|毫无疑问|已经确认|事实是|根因就是|"
    r"已经证明|板上钉钉|毋庸置疑|"
    r"Critical\s*[:：]\s*\d|"
    r"the\s+root\s+cause\s+is|"
    r"definitely\s+(is|caused)|"
    r"without\s+a\s+doubt)",
    re.I,
)
_CVE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.I)
_URL = re.compile(r"https?://[^\s\)\]\>\"']+", re.I)
_STACK = re.compile(
    r"(Traceback\s*\(most\s+recent\s+call\s+last\)|"
    r'File\s+"[^"]+",\s*line\s+\d+|'
    r"at\s+[\w.$]+\([\w./]+:\d+\)|"
    r"Exception\s+in\s+thread|"
    r"Caused\s+by:\s+\w+Error)",
    re.I,
)
_COMMIT = re.compile(r"\b[0-9a-f]{40}\b|\b[0-9a-f]{7,12}\b(?=\s*(已合|merged|fix|修复))", re.I)
_API_ROUTE = re.compile(
    r"(?<![\w])(/api/v\d+/[A-Za-z0-9_./\-{}]+|"
    r"/internal/[A-Za-z0-9_./\-{}]+|"
    r"/admin/[A-Za-z0-9_./\-{}]{6,})",
    re.I,
)
_VERSION_PIN = re.compile(
    r"\b([a-zA-Z][\w.-]{1,40})==(\d+\.\d+(?:\.\d+)?(?:[a-zA-Z0-9._-]*)?)\b|"
    r"(?:当前|正式|生产|latest)\s*(?:版本|version)\s*(?:为|是|=|：|:)?\s*"
    r"v?(\d+\.\d+\.\d+(?:[-+][\w.]+)?)",
    re.I,
)
_GIFT_WRAP = re.compile(
    r"(已修复|已经修复|已完成修复|已经修好|修复完毕|"
    r"已确认完成|任务已完成|已经解决|"
    r"already\s+fixed|fix\s+is\s+done|shipped\s+the\s+fix)",
    re.I,
)
_SOURCE_CLAIM = re.compile(
    r"(文档(中|里|上)?(写到|写着|提到|规定|说明)|"
    r"手册(写|规定)|官方(文档|说明)(称|说)|"
    r"根据(该|此|上述)?文档|"
    r"the\s+docs?\s+(say|state|mention)|"
    r"according\s+to\s+(the\s+)?(docs?|manual|readme))",
    re.I,
)
_LINE_CLAIM = re.compile(
    r"(第\s*\d{1,5}\s*行|line\s+\d{1,5})\s*"
    r"(存在|有|是|为|导致|造成|引入)",
    re.I,
)
_VERIFY_HINT = re.compile(
    r"(核实|验证|verify|confirm|probe|探路|自行|"
    r"glob|grep|file_read|web_search|不要假设|路径不存在|"
    r"未核实|if\s+missing|do\s+not\s+assume)",
    re.I,
)


@dataclass
class DispatchRisk:
    ok: bool
    severity: str = "ok"  # ok | warn | block
    reasons: list[str] = field(default_factory=list)
    missing_paths: list[str] = field(default_factory=list)
    phantom_names: list[str] = field(default_factory=list)
    hard_numbers: list[str] = field(default_factory=list)
    hard_counts: list[str] = field(default_factory=list)
    cve_ids: list[str] = field(default_factory=list)
    stack_traces: list[str] = field(default_factory=list)
    api_routes: list[str] = field(default_factory=list)
    version_pins: list[str] = field(default_factory=list)
    commit_hashes: list[str] = field(default_factory=list)
    latest_claims: bool = False
    certain_claims: bool = False
    gift_wrap: bool = False
    source_claims: bool = False
    line_claims: bool = False
    urls: list[str] = field(default_factory=list)
    rewritten_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "severity": self.severity,
            "reasons": list(self.reasons),
            "missing_paths": list(self.missing_paths),
            "phantom_names": list(self.phantom_names),
            "hard_numbers": list(self.hard_numbers),
            "hard_counts": list(self.hard_counts),
            "cve_ids": list(self.cve_ids)[:12],
            "stack_traces": list(self.stack_traces)[:4],
            "api_routes": list(self.api_routes)[:8],
            "version_pins": list(self.version_pins)[:8],
            "latest_claims": self.latest_claims,
            "certain_claims": self.certain_claims,
            "gift_wrap": self.gift_wrap,
            "source_claims": self.source_claims,
            "line_claims": self.line_claims,
            "urls": list(self.urls)[:10],
        }


def _path_exists(path: str, roots: list[Path]) -> bool:
    raw = Path(path)
    cands = [raw] if raw.is_absolute() else [r / path for r in roots]
    for c in cands:
        try:
            if c.is_file() or c.is_dir():
                return True
        except OSError:
            continue
    return False


def _basename_exists(base: str, roots: list[Path], cited: list[str]) -> bool:
    """Cheap existence: only check cited paths + shallow known layouts (no rglob)."""
    low = base.lower()
    for p in cited:
        if p.replace("\\", "/").lower().endswith("/" + low) or p.lower().endswith(low):
            if _path_exists(p, roots):
                return True
    # shallow probes under common roots only (depth-limited)
    for r in roots:
        for sub in (
            r / base,
            r / "backend" / base,
            r / "backend" / "agent" / base,
            r / "backend" / "kernel" / base,
            r / "frontend" / base,
            r / "src" / base,
        ):
            try:
                if sub.is_file():
                    return True
            except OSError:
                continue
    return False


def scan_dispatch_instruction(instruction: str) -> DispatchRisk:
    """Analyze CEO→worker instruction for multi-class hallucination risk."""
    text = (instruction or "").strip()
    risk = DispatchRisk(ok=True)
    if not text:
        risk.ok = False
        risk.severity = "block"
        risk.reasons.append("empty_instruction")
        return risk

    roots = project_roots()
    paths = extract_cited_paths(text)
    missing = [p for p in paths if not _path_exists(p, roots)]
    risk.missing_paths = missing

    # template modules / fragments
    low = text.replace("\\", "/").lower()
    phantoms: list[str] = []
    for frag in _PHANTOM_FRAGMENTS:
        if frag.lower() in low:
            # only if path not actually present
            if not _path_exists(frag, roots) and not any(
                frag.lower() in p.replace("\\", "/").lower() and _path_exists(p, roots)
                for p in paths
            ):
                phantoms.append(frag)
    for base in _PHANTOM_BASENAMES:
        if re.search(rf"(?<![\w/]){re.escape(base)}(?![\w])", text, re.I):
            if not _basename_exists(base, roots, paths):
                phantoms.append(base)
    risk.phantom_names = sorted(set(phantoms))

    for m in _HARD_NUM.finditer(text):
        risk.hard_numbers.append(m.group(0)[:80])
    for m in _HARD_COUNT.finditer(text):
        risk.hard_counts.append(m.group(0)[:80])

    risk.latest_claims = bool(_LATEST.search(text))
    risk.certain_claims = bool(_CERTAIN.search(text))
    risk.gift_wrap = bool(_GIFT_WRAP.search(text))
    risk.source_claims = bool(_SOURCE_CLAIM.search(text))
    risk.line_claims = bool(_LINE_CLAIM.search(text))
    risk.urls = _URL.findall(text)[:15]
    risk.cve_ids = list(dict.fromkeys(_CVE.findall(text)))[:20]
    risk.stack_traces = [m.group(0)[:120] for m in _STACK.finditer(text)][:5]
    risk.api_routes = list(dict.fromkeys(m.group(1) for m in _API_ROUTE.finditer(text)))[:12]
    risk.version_pins = [m.group(0)[:60] for m in _VERSION_PIN.finditer(text)][:10]
    risk.commit_hashes = list(dict.fromkeys(_COMMIT.findall(text)))[:8]

    has_verify_room = bool(_VERIFY_HINT.search(text))

    # ── score reasons ─────────────────────────────────────
    if missing:
        risk.reasons.append(
            f"phantom_paths:{len(missing)} " + ", ".join(f"`{p}`" for p in missing[:8])
        )
    if risk.phantom_names:
        risk.reasons.append("template_modules:" + ", ".join(risk.phantom_names[:8]))
    if risk.hard_numbers:
        risk.reasons.append(
            "hard_metrics_as_facts:" + "; ".join(risk.hard_numbers[:5])
        )
    if risk.hard_counts:
        risk.reasons.append(
            "hard_counts_as_facts:" + "; ".join(risk.hard_counts[:5])
        )
    if risk.stack_traces:
        risk.reasons.append(f"stack_trace_in_ticket:{len(risk.stack_traces)}")
    if len(risk.cve_ids) >= 2 and not has_verify_room:
        risk.reasons.append(f"many_cve_ids_unverified:{len(risk.cve_ids)}")
    elif len(risk.cve_ids) >= 1 and risk.certain_claims and not has_verify_room:
        risk.reasons.append(f"cve_as_certain_fact:{risk.cve_ids[0]}")
    if risk.latest_claims:
        risk.reasons.append("latest_realtime_claims")
    if risk.certain_claims:
        risk.reasons.append("absolute_certainty_without_evidence")
    if risk.gift_wrap:
        risk.reasons.append("gift_wrap_done_claims")
    if risk.source_claims and not paths and not has_verify_room:
        risk.reasons.append("source_attribution_without_path")
    if risk.line_claims and missing:
        risk.reasons.append("line_claims_on_phantom_paths")
    if risk.api_routes and not has_verify_room:
        risk.reasons.append(
            "concrete_api_routes:" + ", ".join(risk.api_routes[:5])
        )
    if risk.version_pins and not has_verify_room:
        risk.reasons.append(
            "version_pins_as_facts:" + "; ".join(risk.version_pins[:4])
        )
    if risk.commit_hashes and risk.certain_claims:
        risk.reasons.append("commit_hash_as_certain_fix")
    if len(risk.urls) > 5 and not has_verify_room:
        risk.reasons.append(f"many_urls_unverified:{len(risk.urls)}")

    # ── severity (policy-aware: soft default → prefer warn over block) ──
    try:
        from backend.agent.grounding_policy import get_policy

        pol = get_policy()
    except Exception:
        from backend.agent.grounding_policy import GroundingPolicy

        pol = GroundingPolicy(mode="soft", model_tier="default")

    hard_bits: list[str] = []
    if pol.block_missing_paths and missing:
        hard_bits.append("paths")
    if pol.block_template_modules and risk.phantom_names:
        hard_bits.append("templates")
    if pol.block_hard_metrics and (risk.hard_numbers or risk.hard_counts):
        hard_bits.append("metrics")
    if pol.block_stack_traces and risk.stack_traces:
        hard_bits.append("stack")
    if pol.block_multi_cve and any(
        r.startswith("many_cve") or r.startswith("cve_as_certain") for r in risk.reasons
    ):
        hard_bits.append("cve")
    if (
        pol.block_latest_certain_combo
        and risk.latest_claims
        and risk.certain_claims
        and not has_verify_room
    ):
        hard_bits.append("latest_certain")
    if risk.line_claims and missing and pol.block_missing_paths:
        hard_bits.append("line_on_phantom")

    soft_warn = bool(
        risk.hard_numbers
        or risk.hard_counts
        or risk.stack_traces
        or risk.latest_claims
        or risk.certain_claims
        or risk.gift_wrap
        or (risk.source_claims and not paths)
        or risk.api_routes
        or risk.version_pins
        or risk.commit_hashes
        or len(risk.urls) > 5
        or risk.cve_ids
        or (risk.phantom_names and not pol.block_template_modules)
        or (missing and not pol.block_missing_paths)
    )

    if hard_bits:
        risk.ok = False
        risk.severity = "block"
        if "metrics" in hard_bits:
            risk.reasons.append("rewrite_metrics_as_goals_not_facts")
    elif soft_warn:
        risk.severity = "warn"
        risk.ok = True
        # CEO already left verify room → drop to ok for mild signals
        mild_only = not (
            risk.hard_numbers
            or risk.hard_counts
            or risk.stack_traces
            or risk.cve_ids
            or risk.gift_wrap
        )
        if has_verify_room and mild_only:
            risk.severity = "ok"
    else:
        risk.severity = "ok"
        risk.ok = True

    risk.rewritten_hint = _suggest_rewrite(text, risk)
    return risk


def _suggest_rewrite(text: str, risk: DispatchRisk) -> str:
    """Short soft rewrite hints (not a script the CEO must follow)."""
    parts = [
        "建议：工单用「目标+范围」，具体路径/数字/CVE/最新结论留给员工工具核实。",
    ]
    if risk.missing_paths:
        parts.append("不存在的路径：" + ", ".join(risk.missing_paths[:6]))
    if risk.phantom_names:
        parts.append("疑似模板模块名：" + ", ".join(risk.phantom_names[:6]))
    if risk.hard_numbers or risk.hard_counts:
        parts.append("量化结论宜写成待统计目标，而非既成事实。")
    if risk.cve_ids:
        parts.append("CVE 作调查线索即可，勿写成已确认。")
    if risk.stack_traces:
        parts.append("堆栈可作线索；建议员工自行复现收集。")
    if risk.latest_claims:
        parts.append("「最新」类请让员工 web_search，勿写死结论。")
    if risk.gift_wrap:
        parts.append("「已修复」宜改为待办与验收标准。")
    return "\n".join(parts)


def format_block_message(risk: DispatchRisk) -> str:
    lines = [
        "[Error] 工单未通过落地校验（疑似未核实路径/模板模块会直接污染员工）。",
        f"severity={risk.severity}",
    ]
    for r in risk.reasons[:8]:
        lines.append(f"- {r}")
    if risk.missing_paths:
        lines.append("不存在的路径：")
        for p in risk.missing_paths[:12]:
            lines.append(f"  ❌ `{p}`")
    if risk.phantom_names:
        lines.append("疑似模板模块名：")
        for p in risk.phantom_names[:8]:
            lines.append(f"  ❌ `{p}`")
    lines.append("")
    lines.append(risk.rewritten_hint)
    lines.append("需要强行派单：force=true（仍会写入校验痕迹供员工参考）。")
    return "\n".join(lines)


def format_warn_message(risk: DispatchRisk) -> str:
    """Soft prefix when assign is allowed but risky."""
    bits = "; ".join(risk.reasons[:4]) if risk.reasons else risk.severity
    return f"（提示·派单校验 warn：{bits}。员工侧会再核实，你也可改写后重派。）\n"


def worker_hygiene_block() -> str:
    """Short workforce note — avoid long ritual that binds strong models."""
    try:
        from backend.agent.grounding_policy import get_policy

        short = get_policy().short_prompts
    except Exception:
        short = True
    if short:
        return (
            "\n【工单线索】路径/数字/CVE/「最新」/「一定是」仅供参考；"
            "以工具输出为准，不存在则写「工单不实」并自行探路。\n"
        )
    return (
        "\n【工单取证纪律】工单中的路径、指标、CVE、堆栈、「最新」断言都是线索。"
        "先核实再写结论；无证据标明「未核实」。\n"
    )


def evaluate_dispatcher_session(
    user_input: str,
    tools_used: list[str],
    final_text: str = "",
    *,
    max_followups_done: int = 0,
    model_name: str | None = None,
) -> tuple[bool, str, str]:
    """CEO 只派单未取证时的策略。

    soft/balanced：默认放行（靠 assign 门 + 员工卫生），不硬打断聪明模型。
    strict：最多硬 followup 一次，文案保持简短。

    Returns (ok, reason, nudge).
    """
    from backend.agent.grounding_policy import get_policy
    from backend.agent.task_grounding import (
        CODE_READ,
        DEEP_CODE,
        KB,
        WEB,
        classify_task,
    )

    pol = get_policy(model_name)
    kind = classify_task(user_input)
    tools = [str(t) for t in tools_used if t]
    toolset = set(tools)
    dispatch_tools = frozenset({"crew_steward", "delegate_task", "agent_call"})
    if not (toolset & dispatch_tools):
        return True, "not_dispatch", ""
    if not kind:
        return True, "ungrounded_user_task", ""
    if toolset & (CODE_READ | DEEP_CODE | WEB | KB | frozenset({"python", "command"})):
        return True, "dispatch_with_evidence", ""
    if not pol.hard_dispatch_without_evidence:
        # soft path: do not force another turn
        return True, "dispatch_soft_allow", ""
    if max_followups_done >= min(1, pol.max_hard_followups):
        return True, "followup_budget", ""
    return (
        False,
        "dispatch_without_grounding",
        (
            f"【轻提示·编排】已派单但本轮未取证（类目={kind}）。"
            "若 instruction 含具体路径/数字，建议先 glob/grep 或 web_search 再 assign；"
            "也可 force=true。不必为仪式重复探索。"
        ),
    )


def scan_report_hallucination_flags(report: str) -> list[str]:
    """Lightweight multi-class flags for epilogue footers (worker/CEO final text)."""
    text = report or ""
    flags: list[str] = []
    if not text.strip():
        return flags
    if _CERTAIN.search(text) and not _VERIFY_HINT.search(text):
        flags.append("absolute_certainty_language")
    if _GIFT_WRAP.search(text):
        flags.append("gift_wrap_done_language")
    cves = _CVE.findall(text)
    if len(cves) >= 3 and "未核实" not in text and "verify" not in text.lower():
        flags.append(f"dense_cve_claims:{len(cves)}")
    if _STACK.search(text) and "command" not in text.lower():
        # stack in report without mentioning tool collection — soft flag
        flags.append("stack_trace_without_tool_mention")
    if _HARD_NUM.search(text) and not re.search(r"(python|stdout|脚本|计算得)", text, re.I):
        flags.append("hard_percent_without_calc_trace")
    return flags
