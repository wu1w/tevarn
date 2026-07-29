"""Multi-category task grounding: classify → tool requirements → post-check.

Covers hallucination-prone task families (audit, research, data/stats, …)
with one policy table so completion_gate / prompts / epilogue stay aligned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# ── tool buckets ──────────────────────────────────────────────

WRITE = frozenset({"file_write", "edit", "apply_patch"})
SHELL = frozenset({"command", "python", "process"})
CODE_READ = frozenset({"file_read", "grep", "glob", "doc_read"})
DEEP_CODE = frozenset({"file_read", "grep", "doc_read"})
WEB = frozenset({"web_search", "web_fetch", "search", "http", "browser", "fetch_webpage"})
DATA_EXEC = frozenset({"python", "command", "file_read", "doc_read", "grep"})
LISTISH = frozenset({"glob", "current_time", "use_tool_pack", "list_devices_tool"})
KB = frozenset({"search_knowledge_base", "doc_read", "file_read", "grep", "session_search"})


@dataclass(frozen=True)
class TaskKindSpec:
    id: str
    label_zh: str
    # at least one of these sets must be "satisfied"
    require_any_of: tuple[frozenset[str], ...] = ()
    min_evidence: int = 1
    min_deep: int = 0  # deep = CODE_READ - glob, or WEB hits
    deep_tools: frozenset[str] = DEEP_CODE
    followup_budget: int = 2
    extra_iters: int = 0  # workforce loop boost
    block_empty_tools: bool = True
    block_list_only: bool = True
    long_report_needs_deep: bool = False
    long_report_chars: int = 300
    nudge_no_tools: str = ""
    nudge_shallow: str = ""


# Ordered: first match wins (more specific patterns first)
_KIND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "health_check",
        re.compile(
            r"(健康检查|系统体检|全量巡检|集成健康|health\s*check|"
            r"前端与集成|后端模块代码健康|kernel\s*与工作流|测试套件检查)",
            re.I,
        ),
    ),
    (
        "audit",
        re.compile(
            r"(审计|安全审计|代码审查|code\s*review|漏洞|竞态|泄漏|静态分析|"
            r"security\s*audit|architecture\s*audit|第二轮审计|代码审计|"
            r"review\s+(the\s+)?code|findings|threat\s*model|渗透|CVE|巡检)",
            re.I,
        ),
    ),
    (
        "research",
        re.compile(
            r"(联网|搜索|检索|调研|最新消息|最新版|新闻|今天天气|今天新闻|"
            r"当前.*?(价格|汇率|天气|版本|行情)|"
            r"web\s*search|look\s*up|research|what\s+is\s+the\s+latest|"
            r"(?<![排检])查一下|(?<![检])搜一下|有没有新|实时数据|实时行情|"
            r"today'?s|breaking\s+news)",
            re.I,
        ),
    ),
    (
        "data_stats",
        re.compile(
            r"(统计|数据分析|数据报表|汇总表|占比|百分|均值|平均|中位|方差|"
            r"相关系数|回归|图表数据|CSV|Excel|透视|计数有多少|"
            r"how\s+many|average|median|percentile|histogram|aggregate|"
            r"算一下.*数|计算.*比例|趋势分析)",
            re.I,
        ),
    ),
    (
        "math",
        re.compile(
            r"(计算|算一下|换算|求导|积分|方程|概率|排列组合|"
            r"calculate|compute|math\b|how\s+much\s+is|单位换算)",
            re.I,
        ),
    ),
    (
        "doc_qa",
        re.compile(
            r"(根据(这个|该|以下)?(文件|文档|手册|PDF|说明)|总结(一下)?(这|该)?(篇|个)?|"
            r"解读|摘录|文档里|readme|依据.*回答|基于.*材料|"
            r"summarize\s+(this|the)\s+(doc|file|pdf|readme)|"
            r"what\s+does\s+.{0,40}\s+say)",
            re.I,
        ),
    ),
    (
        "inventory",
        re.compile(
            r"(列出|有哪些|清单|盘点|inventory|list\s+all|"
            r"当前(有哪些|运行|在跑)|员工列表|工单列表|进程列表|"
            r"who\s+is\s+running|status\s+of\s+(all|crew|jobs))",
            re.I,
        ),
    ),
    (
        "diagnose",
        re.compile(
            r"(为什么|排查|诊断|挂了|报错|失败原因|root\s*cause|"
            r"debug|troubleshoot|不工作|起不来|502|503|超时原因|"
            r"怎么回事|定位问题)",
            re.I,
        ),
    ),
    (
        "compare",
        re.compile(
            r"(对比|比较|vs\.?|versus|哪个更好|优劣|差异分析|"
            r"compare\s+.+\s+(and|vs|with)|pros\s+and\s+cons)",
            re.I,
        ),
    ),
    (
        "cite_fact",
        re.compile(
            r"(引用|出处|来源|事实核查|fact\s*check|cite|bibliography|"
            r"有依据吗|是否属实|辟谣)",
            re.I,
        ),
    ),
    (
        "fix",
        re.compile(
            r"(修.{0,8}bug|修复|fix(?:ing)?\s*bug|off-?by-?one|broken|"
            r"failing\s*test|pytest.*fail|改代码|打\s*patch)",
            re.I,
        ),
    ),
    (
        "build",
        re.compile(
            r"(建包|创建包|scaffold|package|gen_pkg|从零|new\s+package|写一个包|实现包)",
            re.I,
        ),
    ),
    (
        "find",
        re.compile(
            r"(找针|SECRET|checksum|needle|在大文件|corpus|搜索秘密)",
            re.I,
        ),
    ),
]

_SPECS: dict[str, TaskKindSpec] = {
    "health_check": TaskKindSpec(
        id="health_check",
        label_zh="系统体检/健康检查",
        require_any_of=(CODE_READ | LISTISH,),
        min_evidence=3,
        min_deep=2,
        deep_tools=DEEP_CODE,
        followup_budget=2,
        extra_iters=10,
        long_report_needs_deep=True,
        long_report_chars=400,
        block_list_only=False,  # 允许以清单为主，但禁止无工具
        nudge_no_tools=(
            "【完成校验·体检】禁止空口写「系统正常/全绿」。\n"
            "请先 glob/list 真实路径（默认跳过 node_modules），再抽样 file_read；"
            "范围过大请只做目录清单+风险点，勿通读全仓。"
        ),
        nudge_shallow=(
            "【完成校验·体检】证据不足。请继续 glob/grep 关键目录，"
            "未完成的检查项必须标「未完成/预算不足」，禁止用报告框架冒充结论。"
        ),
    ),
    "audit": TaskKindSpec(
        id="audit",
        label_zh="代码/安全审计",
        require_any_of=(CODE_READ | SHELL,),
        min_evidence=3,
        min_deep=2,
        deep_tools=DEEP_CODE,
        followup_budget=3,
        extra_iters=12,
        long_report_needs_deep=True,
        nudge_no_tools=(
            "【完成校验·审计】禁止不打开仓库就写 findings。\n"
            "请 glob → grep → file_read，每条 High/Medium 对应真实路径；"
            "找不到写「未在仓库中找到」。"
        ),
        nudge_shallow=(
            "【完成校验·审计】读码不足。请多次 grep + file_read，"
            "禁止编造 orchestrator.py / session_manager.py / tool_execution.py 等模板路径。"
        ),
    ),
    "research": TaskKindSpec(
        id="research",
        label_zh="联网检索/调研",
        require_any_of=(WEB, KB | CODE_READ),
        min_evidence=2,
        min_deep=1,
        deep_tools=WEB | KB,
        followup_budget=3,
        extra_iters=6,
        long_report_needs_deep=True,
        long_report_chars=400,
        nudge_no_tools=(
            "【完成校验·检索】这是检索/调研任务，禁止凭记忆编造「最新」事实。\n"
            "请用 web_search / web_fetch（或知识库检索）获取来源后再回答，并注明出处。"
        ),
        nudge_shallow=(
            "【完成校验·检索】检索轮次不足。请至少完成 2 次有效搜索/抓取，"
            "综合后再写结论；不要用训练数据冒充实时信息。"
        ),
    ),
    "data_stats": TaskKindSpec(
        id="data_stats",
        label_zh="数据/统计",
        require_any_of=(DATA_EXEC,),
        min_evidence=2,
        min_deep=1,
        deep_tools=frozenset({"python", "command", "file_read", "doc_read"}),
        followup_budget=3,
        extra_iters=8,
        long_report_needs_deep=True,
        nudge_no_tools=(
            "【完成校验·数据】统计/数据分析必须基于实际数据或可复算脚本。\n"
            "请 file_read 读数据，或 python/command 计算后再报数字；禁止口头估一个百分比。"
        ),
        nudge_shallow=(
            "【完成校验·数据】尚未用工具算出结果。请用 python 或命令行完成计算，"
            "并在回答中给出可核对的中间量（样本数、公式或脚本输出摘要）。"
        ),
    ),
    "math": TaskKindSpec(
        id="math",
        label_zh="计算/换算",
        require_any_of=(frozenset({"python", "command"}),),
        min_evidence=1,
        min_deep=1,
        deep_tools=frozenset({"python", "command"}),
        followup_budget=2,
        extra_iters=4,
        nudge_no_tools=(
            "【完成校验·计算】请用 python 或 command 实际运算后再给出答案，不要心算编造。"
        ),
        nudge_shallow=(
            "【完成校验·计算】请运行计算工具（python 一行脚本即可）并基于 stdout 作答。"
        ),
    ),
    "doc_qa": TaskKindSpec(
        id="doc_qa",
        label_zh="文档问答/总结",
        require_any_of=(CODE_READ | KB,),
        min_evidence=2,
        min_deep=1,
        deep_tools=DEEP_CODE | KB,
        followup_budget=3,
        extra_iters=6,
        long_report_needs_deep=True,
        nudge_no_tools=(
            "【完成校验·文档】请先 file_read/doc_read 打开指定材料，再总结或回答；"
            "禁止脱离原文编造章节内容。"
        ),
        nudge_shallow=(
            "【完成校验·文档】阅读不足。请继续读取相关文件/段落，引用原文关键句后再下结论。"
        ),
    ),
    "inventory": TaskKindSpec(
        id="inventory",
        label_zh="清单/状态盘点",
        require_any_of=(
            CODE_READ | SHELL | frozenset({"crew_steward", "list_devices_tool"}),
            WEB,  # rare
        ),
        min_evidence=1,
        min_deep=1,
        deep_tools=CODE_READ | SHELL | frozenset({"crew_steward", "list_devices_tool", "glob"}),
        followup_budget=2,
        extra_iters=4,
        nudge_no_tools=(
            "【完成校验·清单】请用工具列出真实状态（glob/command/API/员工列表），"
            "禁止凭印象报「大概有几个」。"
        ),
        nudge_shallow=(
            "【完成校验·清单】请实际查询后再列清单，并说明数据来源（哪次工具输出）。"
        ),
    ),
    "diagnose": TaskKindSpec(
        id="diagnose",
        label_zh="排查/诊断",
        require_any_of=(CODE_READ | SHELL,),
        min_evidence=2,
        min_deep=1,
        deep_tools=DEEP_CODE | SHELL,
        followup_budget=3,
        extra_iters=8,
        long_report_needs_deep=True,
        nudge_no_tools=(
            "【完成校验·排查】请先复现/读日志/读相关代码，再给根因；"
            "禁止空口断定「一定是某某配置」。"
        ),
        nudge_shallow=(
            "【完成校验·排查】证据不足。请 command 看错误输出或 file_read/grep 定位，"
            "再写因果链。"
        ),
    ),
    "compare": TaskKindSpec(
        id="compare",
        label_zh="对比/评估",
        require_any_of=(WEB | CODE_READ | KB,),
        min_evidence=2,
        min_deep=1,
        deep_tools=WEB | DEEP_CODE | KB,
        followup_budget=3,
        extra_iters=6,
        long_report_needs_deep=True,
        nudge_no_tools=(
            "【完成校验·对比】请检索或阅读双方材料后再对比；禁止用刻板印象写优劣表。"
        ),
        nudge_shallow=(
            "【完成校验·对比】请补充对各方的工具取证（文档/代码/网页），再给对照结论。"
        ),
    ),
    "cite_fact": TaskKindSpec(
        id="cite_fact",
        label_zh="事实/出处",
        require_any_of=(WEB | KB | CODE_READ,),
        min_evidence=2,
        min_deep=1,
        deep_tools=WEB | KB | DEEP_CODE,
        followup_budget=3,
        extra_iters=4,
        nudge_no_tools=(
            "【完成校验·事实】请检索或打开原文核对后再断言；无出处则标明「未核实」。"
        ),
        nudge_shallow=(
            "【完成校验·事实】请补充可核对来源（链接/文件路径/工具摘录）。"
        ),
    ),
    "fix": TaskKindSpec(
        id="fix",
        label_zh="修 bug",
        require_any_of=(WRITE,),
        min_evidence=1,
        followup_budget=2,
        extra_iters=4,
        block_list_only=True,
        nudge_no_tools="【完成校验】修 bug 请先读再改，并用工具验证。",
        nudge_shallow="【完成校验】请用 edit/file_write 修改代码并跑测。",
    ),
    "build": TaskKindSpec(
        id="build",
        label_zh="建包/脚手架",
        require_any_of=(WRITE,),
        min_evidence=1,
        followup_budget=2,
        extra_iters=4,
        nudge_no_tools="【完成校验】建包需要 file_write 创建文件。",
        nudge_shallow="【完成校验】请继续写文件并 pytest。",
    ),
    "find": TaskKindSpec(
        id="find",
        label_zh="查找线索",
        require_any_of=(CODE_READ,),
        min_evidence=1,
        min_deep=1,
        deep_tools=DEEP_CODE,
        followup_budget=2,
        nudge_no_tools="【完成校验】请用 grep/file_read 查找后再报告。",
        nudge_shallow="【完成校验】请实际搜索并给出找到的值。",
    ),
}


def classify_task(user_input: str) -> str | None:
    """Return primary task kind id, or None if no special grounding."""
    text = (user_input or "").strip()
    if not text:
        return None
    for kid, pat in _KIND_PATTERNS:
        if pat.search(text):
            return kid
    return None


def classify_all(user_input: str) -> list[str]:
    """All matching kinds (for multi-label footer / prompts)."""
    text = (user_input or "").strip()
    if not text:
        return []
    hits = [kid for kid, pat in _KIND_PATTERNS if pat.search(text)]
    return hits


def get_spec(kind: str | None) -> TaskKindSpec | None:
    if not kind:
        return None
    return _SPECS.get(kind)


def is_grounded_task(user_input: str) -> bool:
    return classify_task(user_input) is not None


def is_audit_like_task(user_input: str) -> bool:
    """Back-compat alias."""
    return classify_task(user_input) == "audit"


def followup_budget_for(user_input: str) -> int:
    kind = classify_task(user_input)
    spec = get_spec(kind)
    return spec.followup_budget if spec else 2


def extra_iterations_for(user_input: str) -> int:
    kind = classify_task(user_input)
    spec = get_spec(kind)
    return int(spec.extra_iters) if spec else 0


def _norm_tools(tools: Iterable[str]) -> list[str]:
    return [str(t) for t in (tools or []) if t]


@dataclass
class GroundingVerdict:
    ok: bool
    kind: str | None = None
    reason: str = ""
    nudge: str = ""


def evaluate_grounding(
    user_input: str,
    tools_used: Iterable[str],
    final_text: str = "",
    *,
    max_followups_done: int = 0,
    model_name: str | None = None,
) -> GroundingVerdict:
    """Tool-sufficiency check — hard only where policy allows (soft default).

    Soft path prefers epilogue footers over force-followup so strong models
    are not trapped in tool rituals.
    """
    from backend.agent.grounding_policy import get_policy

    pol = get_policy(model_name)
    kind = classify_task(user_input)
    if not kind:
        return GroundingVerdict(ok=True, reason="ungrounded_kind")
    spec = get_spec(kind)
    if not spec:
        return GroundingVerdict(ok=True, reason="no_spec")

    budget = min(int(spec.followup_budget), int(pol.max_hard_followups))
    if max_followups_done >= budget:
        return GroundingVerdict(ok=True, kind=kind, reason="followup_budget_exhausted")

    tools = _norm_tools(tools_used)
    toolset = set(tools)
    final = (final_text or "").strip()
    ignore = {"current_time", "clarify", "todo_write", "todo_list", "manage_goal"}
    evidence = [t for t in tools if t not in ignore]
    deep = [t for t in tools if t in spec.deep_tools]
    only_list = bool(tools) and set(tools) <= LISTISH
    bucket_hit = False
    if not spec.require_any_of:
        bucket_hit = True
    else:
        for bucket in spec.require_any_of:
            if toolset & bucket:
                bucket_hit = True
                break

    label = spec.label_zh

    def _soft(reason: str) -> GroundingVerdict:
        """Allow finish; epilogue footer still may annotate."""
        return GroundingVerdict(ok=True, kind=kind, reason=f"soft_{reason}")

    def _hard(reason: str, nudge: str) -> GroundingVerdict:
        return GroundingVerdict(ok=False, kind=kind, reason=reason, nudge=nudge)

    # ── hard tier (always-on for real delivery contracts) ──
    if pol.hard_empty_tools and spec.block_empty_tools and not tools:
        # keep nudge short — avoid multi-paragraph rituals
        nudge = (
            f"【轻提示·{label}】这类任务通常需要工具取证后再下结论。"
            "请调用合适工具，或明确标明「未核实」。"
        )
        return _hard(f"{kind}_no_tools", nudge)

    if pol.hard_fix_build_write and kind == "fix" and not (toolset & WRITE):
        return _hard(
            "fix_without_write",
            "【轻提示·修 bug】若声称已修好，请用 edit/file_write 改代码并尽量跑测。",
        )
    if pol.hard_fix_build_write and kind == "build":
        writes = sum(1 for t in tools if t in WRITE)
        if writes < 1:
            return _hard(
                "build_without_write",
                "【轻提示·建包】请 file_write 创建源码（再 pytest 更佳）。",
            )
        # soft: do not force second write + test cycle for strong models
        if writes == 1 and not (toolset & SHELL) and pol.mode == "strict":
            return _hard(
                "build_single_write_no_test",
                "【轻提示·建包】可再跑 command/pytest 验证。",
            )

    if kind == "find" and final and len(final) < 8 and pol.mode == "strict":
        return _hard(
            "find_empty_report",
            "【轻提示·查找】请给出包含查到值的短报告。",
        )

    # ── soft-demoted checks (strict/balanced only as configured) ──
    if only_list and spec.block_list_only:
        if pol.hard_list_only:
            return _hard(
                f"{kind}_list_only",
                f"【轻提示·{label}】仅列目录通常不够，可再读/搜/算一步。",
            )
        return _soft("list_only")

    if not bucket_hit:
        if pol.hard_wrong_bucket:
            return _hard(
                f"{kind}_wrong_tools",
                f"【轻提示·{label}】可换用更贴合的工具族后再定稿。",
            )
        return _soft("wrong_tools")

    if len(evidence) < spec.min_evidence:
        if pol.hard_shallow:
            return _hard(
                f"{kind}_shallow_tools",
                f"【轻提示·{label}】证据偏少时可再取证；也可标明不确定处。",
            )
        return _soft("shallow_tools")

    if spec.min_deep > 0 and len(deep) < spec.min_deep:
        if pol.hard_few_deep and (max_followups_done < budget or not deep):
            return _hard(
                f"{kind}_few_deep",
                f"【轻提示·{label}】可再做一轮深度读取/检索；非必须堆满工具次数。",
            )
        return _soft("few_deep")

    if (
        spec.long_report_needs_deep
        and final
        and len(final) > spec.long_report_chars
        and not deep
    ):
        if pol.hard_long_report:
            return _hard(
                f"{kind}_report_without_evidence",
                f"【轻提示·{label}】长文缺少工具痕迹时，建议补证据或标「未核实」。",
            )
        return _soft("report_without_evidence")

    if (
        pol.hard_certainty_language
        and final
        and kind in ("diagnose", "audit", "cite_fact", "research")
    ):
        certain = bool(
            re.search(
                r"(一定是|肯定是|毫无疑问|根因就是|已经确认|"
                r"definitely\s+is|root\s+cause\s+is|without\s+a\s+doubt)",
                final,
                re.I,
            )
        )
        gift = bool(
            re.search(
                r"(已修复|已经修好|修复完毕|already\s+fixed|fix\s+is\s+done)",
                final,
                re.I,
            )
        )
        if (certain or gift) and len(deep) < max(1, spec.min_deep):
            return _hard(
                f"{kind}_certainty_without_evidence",
                f"【轻提示·{label}】断定语气较强且证据偏少时可弱化措辞或补工具。",
            )

    return GroundingVerdict(ok=True, kind=kind, reason="ok")


# ── post-check footer ─────────────────────────────────────────

_PATH_RE = re.compile(
    r"(?:"
    r"`([^`\n]{3,220})`"
    r"|"
    r"(?<![A-Za-z0-9_/.-])"
    r"((?:backend|frontend|docs|src|app|lib|crates|tests|scripts)"
    r"(?:/[A-Za-z0-9_./+-]+)+\.(?:py|ts|tsx|js|jsx|go|rs|md|toml|json|yml|yaml))"
    r"|"
    r"((?:[\w.-]+/)+[\w.-]+\.(?:py|ts|tsx|js|jsx|go|rs))"
    r")",
    re.I,
)
_URL_RE = re.compile(r"https?://[^\s\)\]\>\"']+", re.I)
_PCT_RE = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s*%")
_NUM_CLAIM_RE = re.compile(
    r"(约|大约|平均|共计|总共|达到|超过|增长|下降)\s*(\d+(?:\.\d+)?)\s*"
    r"(个|人|次|条|万|亿|元|%|percent)?",
    re.I,
)
_SKIP_PREFIX = ("http://", "https://", "www.", "example.com", "node_modules/")


def extract_cited_paths(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for m in _PATH_RE.finditer(text or ""):
        raw = next((g for g in m.groups() if g), "") or ""
        p = raw.strip().strip("\"'").replace("\\", "/")
        p = re.sub(r":\d+(?:-\d+)?$", "", p)
        p = re.sub(r"#L\d+.*$", "", p)
        if not p or len(p) < 4:
            continue
        if any(p.lower().startswith(s) for s in _SKIP_PREFIX):
            continue
        if "/" not in p and not p.endswith((".py", ".ts", ".tsx", ".js", ".rs", ".go")):
            continue
        if p.count(" ") > 0:
            continue
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(p)
    return found[:80]


def project_roots() -> list[Path]:
    roots: list[Path] = [Path.cwd()]
    try:
        from backend.tools.permissions import resolve_agent_workspace_root

        wr = Path(resolve_agent_workspace_root())
        if wr not in roots:
            roots.insert(0, wr)
    except Exception:
        pass
    try:
        import backend

        repo = Path(backend.__file__).resolve().parent.parent
        if repo not in roots:
            roots.append(repo)
    except Exception:
        pass
    return roots


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


def annotate_grounded_report(
    report: str,
    *,
    user_input: str = "",
    tools_used: Iterable[str] | None = None,
    force_kinds: list[str] | None = None,
) -> str:
    """Append machine-check footer for grounded task outputs."""
    text = report or ""
    if not text.strip():
        return text
    kinds = force_kinds or classify_all(user_input)
    # also detect report shape
    if not kinds and _looks_like_findings_dump(text):
        kinds = ["audit"]
    if not kinds:
        return text

    tools = [str(t) for t in (tools_used or []) if t]
    toolset = set(tools)
    lines = ["", "---", "## ⚠ 落地校验（系统自动，非模型自述）"]
    lines.append(f"识别任务类目：{' · '.join(kinds)}")
    changed = False

    # path check for code-ish kinds
    if any(k in kinds for k in ("audit", "doc_qa", "diagnose", "fix", "build", "inventory")):
        roots = project_roots()
        paths = extract_cited_paths(text)
        missing = [p for p in paths if not _path_exists(p, roots)]
        ok_n = len(paths) - len(missing)
        if missing:
            changed = True
            lines.append(
                f"引用路径：可解析 {ok_n}，**不存在 {len(missing)}**（疑似幻觉，勿直接按此改代码）："
            )
            for p in missing[:20]:
                lines.append(f"- ❌ `{p}`")
            if len(missing) > 20:
                lines.append(f"- … 另有 {len(missing) - 20} 条")
        elif paths:
            lines.append(f"引用路径存在性：通过（{ok_n} 个）。")

    # research / cite: URL or search tools
    if any(k in kinds for k in ("research", "cite_fact", "compare")):
        urls = _URL_RE.findall(text)
        used_web = bool(toolset & (WEB | KB))
        if not used_web:
            changed = True
            lines.append(
                "检索/出处类任务：**本回合未使用 web_search/web_fetch/知识库**。"
                "结论中的「最新/实时」信息可信度应下调。"
            )
        if used_web and not urls and len(text) > 200:
            changed = True
            lines.append(
                "已使用检索工具，但正文**未给出可点击来源 URL**。"
                "建议补充链接或检索摘要以便核对。"
            )
        if urls:
            lines.append(f"正文含 URL {len(urls)} 条（未逐一探活，请人工抽查）。")

    # data/stats / math: numbers without compute tools
    if any(k in kinds for k in ("data_stats", "math")):
        has_nums = bool(_PCT_RE.search(text) or _NUM_CLAIM_RE.search(text) or re.search(r"\b\d{2,}\b", text))
        used_calc = bool(toolset & frozenset({"python", "command"}))
        used_data = bool(toolset & DATA_EXEC)
        if has_nums and not used_calc and "math" in kinds:
            changed = True
            lines.append(
                "计算类回答含数字，但**未运行 python/command**。"
                "数值可能心算/幻觉，请要求可复算脚本输出。"
            )
        if has_nums and not used_data and "data_stats" in kinds:
            changed = True
            lines.append(
                "统计类回答含量化表述，但**缺少读数/计算工具痕迹**。"
                "请核对是否基于真实文件或脚本 stdout。"
            )
        if used_calc:
            lines.append("已使用计算类工具（python/command）。")

    # diagnose / audit: certainty language without deep tools
    if any(k in kinds for k in ("diagnose", "audit", "cite_fact")):
        certain = bool(
            re.search(
                r"(一定是|肯定是|毫无疑问|根因就是|已经确认|事实是|"
                r"definitely\s+is|root\s+cause\s+is)",
                text,
                re.I,
            )
        )
        deep_ok = bool(toolset & (DEEP_CODE | SHELL | WEB | KB))
        if certain and not deep_ok:
            changed = True
            lines.append(
                "正文含绝对断定用语，但**缺少深度取证工具**。"
                "请将「一定是」改为「可能/待核实」，或补证据。"
            )

    # audit: dense CVE without tool trail
    if "audit" in kinds:
        cves = re.findall(r"\bCVE-\d{4}-\d{4,7}\b", text, flags=re.I)
        if len(cves) >= 3 and not (toolset & (WEB | CODE_READ)):
            changed = True
            lines.append(
                f"报告含 {len(cves)} 条 CVE 编号，但本回合几乎无读码/检索痕迹——"
                "CVE 列表可信度应下调。"
            )

    # inventory: count claims without tools
    if "inventory" in kinds:
        countish = bool(
            re.search(r"(共\s*\d+|一共\s*\d+|\d+\s*个(员工|进程|工单|文件))", text)
        )
        if countish and not tools:
            changed = True
            lines.append(
                "清单类回答含具体数量，但**零工具调用**——数量可能为印象幻觉。"
            )

    # multi-class soft flags — cap volume so footer stays a hint, not a lecture
    try:
        from backend.agent.dispatch_grounding import scan_report_hallucination_flags

        flags = scan_report_hallucination_flags(text)[:3]
        if flags:
            changed = True
            lines.append("多类软信号：" + ", ".join(f"`{fl}`" for fl in flags))
    except Exception:
        pass

    # generic tool emptiness for any grounded kind with long answer
    if len(text) > 400 and not tools:
        changed = True
        lines.append("长回答且**零工具调用**——高幻觉风险，建议重跑并强制取证。")

    if not changed and len(lines) <= 4:
        # only header — skip footer noise for clean OK short answers
        if not any(k in kinds for k in ("audit", "research", "data_stats")):
            return text
        lines.append("基础检查：未发现明显路径/工具空洞（仍建议抽查关键结论）。")

    footer = "\n".join(lines)
    if "落地校验（系统自动" in text:
        return text
    return text.rstrip() + "\n" + footer + "\n"


def _looks_like_findings_dump(text: str) -> bool:
    if len(text or "") < 400:
        return False
    hits = sum(1 for kw in ("High", "Medium", "Critical", "🔴", "🟡", "Finding", "漏洞") if kw in text)
    return hits >= 2 and bool(extract_cited_paths(text))


def maybe_annotate_report(
    user_input: str,
    report: str,
    tools_used: Iterable[str] | None = None,
) -> str:
    return annotate_grounded_report(
        report, user_input=user_input or "", tools_used=tools_used
    )


def grounding_prompt_block() -> str:
    """Compact grounding guidance — long checklists lower strong-model quality."""
    try:
        from backend.agent.grounding_policy import get_policy

        short = get_policy().short_prompts
    except Exception:
        short = True
    if short:
        return (
            "# Evidence (lightweight)\n"
            "Prefer tools when claims need ground truth: code paths, live facts, "
            "stats/math, root causes, doc quotes. No evidence → say so; "
            "don't invent paths/CVE/metrics. "
            "When assigning work: goal+scope beats unverified details; "
            "workers re-check tickets."
        )
    return (
        "# Evidence by task (when applicable)\n"
        "Audit/research/data/math/doc/diagnose/compare: use tools before hard claims. "
        "Dispatch: don't poison workers with phantom paths or fake metrics. "
        "No evidence → no high-confidence claim."
    )


# back-compat names used by older imports
def annotate_audit_report(
    report: str,
    *,
    user_input: str = "",
    tools_used: Iterable[str] | None = None,
    force: bool = False,
) -> str:
    kinds = ["audit"] if force else None
    return annotate_grounded_report(
        report, user_input=user_input, tools_used=tools_used, force_kinds=kinds
    )


def maybe_annotate_audit_report(
    user_input: str,
    report: str,
    tools_used: Iterable[str] | None = None,
) -> str:
    return maybe_annotate_report(user_input, report, tools_used)
