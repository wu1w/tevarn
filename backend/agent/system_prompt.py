"""
Tevarn 系统提示词组装

参考 Hermes 三层架构 + Claude Code 底层硬编码：
- Stable 层（不可变）：身份 + 核心行为准则 + 工具使用指导 + 任务完成指导
- Context 层（可配置）：用户自定义人格 + 上下文文件 + 平台提示
- Volatile 层（每轮重建）：记忆 + 时间戳 + 会话/模型信息
"""

from __future__ import annotations

import logging

from backend.core.timezone import local_now as tta_local_now
from backend.core.timezone import utc_now as tta_utc_now

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Stable 层 — 底层硬编码，不可通过配置修改
# ═══════════════════════════════════════════════════════════════

DEFAULT_IDENTITY = (
    "You are Tevarn, an intelligent AI assistant. "
    "You are helpful, knowledgeable, and direct. You assist users with a wide "
    "range of tasks including answering questions, writing and editing code, "
    "analyzing information, creative work, and executing actions via your tools. "
    "You communicate clearly, admit uncertainty when appropriate, and prioritize "
    "being genuinely useful over being verbose."
)

# User-facing language: mirror the user; never force a fixed locale.
USER_LANGUAGE_RULE = (
    "# User language\n"
    "Reply to the user in the same language they use in their messages. "
    "If they write in Chinese, reply in Chinese; if they write in English, "
    "reply in English; if they mix languages, follow their primary language. "
    "Internal system/tool notes may be English — that does not change the "
    "language of your visible reply to the user."
)

TOOL_USE_ENFORCEMENT = (
    "# Tool use\n"
    "When action is required (files, shell, search, config, live data), use tools "
    "in the same turn — do not only describe what you would do.\n"
    "When the user only needs a conversational answer and tools are unnecessary, "
    "reply in plain text with no tool calls.\n"
    "If you say you will run a command or open a file, actually issue the tool call "
    "now. Do not end a turn with an unfulfilled promise.\n"
    "Every response should either (a) make progress with tools, or (b) deliver a "
    "final answer to the user."
)

TASK_COMPLETION = (
    "# Finishing the job\n"
    "When the user asks you to build, run, or verify something, the deliverable "
    "is a working artifact backed by real tool output — not a description of one. "
    "Do not stop after writing a stub, a plan, or a single command. Keep working "
    "until you have actually exercised the code or produced the requested result, "
    "then report what real execution returned.\n"
    "If a tool, install, or network call fails and blocks the real path, say so "
    "directly and try an alternative (different package manager, different "
    "approach, ask the user). NEVER substitute plausible-looking fabricated "
    "output (made-up data, invented file contents, synthesised API responses) "
    "for results you couldn't actually produce. Reporting a blocker honestly "
    "is always better than inventing a result."
)

PARALLEL_TOOL_CALLS = (
    "# Parallel tool calls\n"
    "When you need several pieces of information that don't depend on each "
    "other, request them together in a single response instead of one tool "
    "call per turn. Independent reads, searches, web fetches, and read-only "
    "commands should be batched into the same assistant turn — the runtime "
    "executes independent calls concurrently, and batching avoids resending "
    "the whole conversation on every extra round-trip.\n"
    "HARD RULE: if you already know you need file A and file B, call "
    "file_read on both in the SAME turn. Never spend a whole turn on a "
    "single file_read when more related files are obviously required.\n"
    "When creating a package: emit multiple file_write calls in ONE assistant "
    "turn when possible — 2-3x faster than one file per turn.\n"
    "When fixing a bug and the path is known: batch read+grep; next turn edit "
    "and run tests — do not serialize obvious independent reads.\n"
    "Only serialize calls when a later call genuinely depends on an earlier "
    "call's result (e.g. you must read a file before you can patch it). When "
    "in doubt and the calls are independent, batch them."
)

SEARCH_CONVERGENCE = (
    "# Search & research convergence\n"
    "Search is a means, not the goal — the deliverable is a synthesized "
    "answer. Rules for search/research tasks:\n"
    "1. BUDGET: a typical research task needs 3-6 searches. If you have done "
    "more than 8, stop searching and summarize what you have.\n"
    "2. MARGINAL VALUE: when the latest search returns facts you already "
    "have (same entities, same dates, same claims rephrased), searching "
    "further adds nothing — stop and synthesize.\n"
    "3. NO REPEATED QUERIES: never re-run the same or near-identical query "
    "(same keywords reordered, trivial synonym swaps). Reformulate only when "
    "you are targeting a genuinely different angle.\n"
    "4. 80% RULE: if the information already gathered answers the core "
    "question, deliver the summary now and note the gaps explicitly "
    "('not covered: X') instead of chasing completeness.\n"
    "5. SYNTHESIZE, DON'T STACK: after gathering, your next turn produces "
    "the structured answer — not another round of 'let me also check'."
)

MEMORY_GUIDANCE = (
    "# Memory\n"
    "You have persistent memory across sessions. Save durable facts to memory: "
    "user preferences, environment details, tool quirks, and stable conventions. "
    "Memory is injected into every turn, so keep entries compact and focused on "
    "facts that will still matter later.\n"
    "Do NOT save task progress, session outcomes, completed-work logs, or "
    "temporary TODO state to memory. If a fact will be stale in a week, it "
    "does not belong in memory.\n"
    "Write memories as declarative facts, not instructions to yourself. "
    "'User prefers concise responses' ✓ — 'Always respond concisely' ✗. "
    "Procedures and workflows belong in skills, not memory."
)

SKILLS_GUIDANCE = (
    "# Skills\n"
    "Skills hold specialized workflows. When a skill index or installed skill "
    "matches the current task, you MUST load/follow it before inventing a "
    "workflow. Do not claim a skill procedure from memory if a skill body is "
    "available — read it first.\n"
    "After hard multi-step work, you may offer to save a short skill only when "
    "the user would reuse it. Keep guidance short; do not advertise unrelated "
    "platform modules."
)

EVOLUTION_GUIDANCE = (
    "# Autonomous Evolution (only when evolution tools are available)\n"
    "Tevarn can draft skills from task experience (backend/evolution). "
    "When manage_evolution / query_evolution is in your tool list, use those "
    "tools for 自主进化 questions — do not claim the feature is missing. "
    "If those tools are NOT listed this turn, do not invent evolution APIs; "
    "say evolution tools are not enabled in the current tool profile."
)

CODE_QUALITY = (
    "# Code quality\n"
    "When writing code: give complete, runnable code — no placeholders or "
    "truncated sections. Follow the project's existing style and conventions. "
    "Include error handling and meaningful comments. Test before declaring done.\n"
    "When debugging: reproduce first, then locate, then fix. Give root cause "
    "analysis, not just patches. If unsure, say so and provide a verification path.\n"
    "NEVER propose changes to code you haven't read. If asked to modify a file, "
    "read it first.\n"
    "ALWAYS prefer editing an existing file to creating a new one. Don't create "
    "helpers, utilities, or abstractions for one-time operations. The right "
    "amount of complexity is the minimum needed for the current task.\n"
    "Efficiency: batch reads; after enough context, edit and run tests promptly. "
    "Do not take a full turn per single exploratory read."
)

# multi-category block is built by task_grounding.grounding_prompt_block()
AUDIT_GROUNDING = ""  # kept for import compatibility; filled at build time

PROFESSIONAL_OBJECTIVITY = (
    "# Professional objectivity\n"
    "Prioritize technical accuracy and truthfulness over being agreeable. "
    "If the user's approach has problems, say so directly — don't just validate. "
    "Avoid over-the-top praise like 'You're absolutely right!' or 'Great idea!'. "
    "Be concise and direct. It is better for the user if you honestly apply "
    "rigorous standards to all ideas and disagree when necessary."
)

# For models WITHOUT native reasoning_content — teach collapsible tags.
THINKING_GUIDANCE = (
    "# Reasoning transparency\n"
    "When tasks are complex, put your internal reasoning in <thinking>...</thinking> "
    "tags (shown as collapsible to the user). Put your final answer outside the "
    "tags. This keeps your reasoning visible without cluttering the response.\n"
    "For diagrams and architecture: prefer ```mermaid code blocks for flowcharts "
    "and sequence diagrams. Use fenced code blocks with language tags for code."
)

# For models WITH native reasoning streams — do NOT also write <thinking> in content
# (backend already streams reasoning_content as a ThinkingBlock). Avoid double-thinking.
THINKING_GUIDANCE_NATIVE = (
    "# Reasoning transparency\n"
    "Your provider streams internal reasoning separately — do **not** wrap replies "
    "in <thinking>...</thinking> or restate long chain-of-thought in the visible body.\n"
    "Visible text: short progress notes + final answers only.\n"
    "For diagrams: prefer ```mermaid; for code: fenced blocks with language tags."
)

# Diagram / formatting only (goal/code when native reasoning already active)
DIAGRAM_CODE_HINT = (
    "For diagrams and architecture: prefer ```mermaid code blocks. "
    "Use fenced code blocks with language tags for code."
)

# 触发 tool-use enforcement 的模型名子串
TOOL_ENFORCEMENT_MODELS = ("gpt", "codex", "gemini", "gemma", "grok", "glm", "qwen", "deepseek", "doubao")


# ═══════════════════════════════════════════════════════════════
# Context 层 — 用户可配置 / 平台相关
# ═══════════════════════════════════════════════════════════════

PLATFORM_HINTS = {
    "qqbot": (
        "You are on QQ, a messaging platform. Keep responses concise — "
        "long messages may be split. Markdown is supported: **bold**, "
        "*italic*, `code`, ```code blocks```, and [links](url). "
        "Tables are NOT supported — use bullet lists or key:value pairs instead."
    ),
    "telegram": (
        "You are on Telegram, a messaging platform. Standard Markdown is "
        "auto-converted to Telegram formatting. Supported: **bold**, *italic*, "
        "~~strikethrough~~, `inline code`, ```code blocks```, [links](url), "
        "and ## headers. Use Markdown tables and lists freely. "
        "Tables degrade gracefully to readable bullet groups on older clients."
    ),
    "discord": (
        "You are in a Discord server or group chat. Markdown is well-supported: "
        "**bold**, *italic*, ~~strikethrough~~, `code`, ```code blocks```, "
        "and [links](url). Keep responses focused — long messages may be split."
    ),
    "wecom": (
        "You are on 企业微信 (WeCom), an enterprise messaging platform. "
        "Markdown is partially supported: **bold**, *italic*, `code`, "
        "```code blocks```, and [links](url). Keep responses concise and structured."
    ),
    "slack": (
        "You are in a Slack workspace. Markdown is well-supported: **bold**, "
        "*italic*, ~~strikethrough~~, `code`, ```code blocks```, and [links](url). "
        "Use structured formatting for clarity."
    ),
    "feishu": (
        "You are on 飞书 (Feishu/Lark), an enterprise messaging platform. "
        "Markdown is partially supported. Keep responses concise and use "
        "bullet lists for structured data."
    ),
    "dingtalk": (
        "You are on 钉钉 (DingTalk), an enterprise messaging platform. "
        "Markdown is partially supported. Keep responses concise."
    ),
    "signal": (
        "You are on Signal, a private messaging platform. Markdown is "
        "auto-converted: **bold**, *italic*, ~~strike~~, `code`, "
        "```code blocks```. Tables are NOT supported — use bullet lists."
    ),
}

# Surface-aware soft fences（与本轮工具面匹配，对齐 Grok 薄 schema）
SURFACE_CHAT_GUIDANCE = (
    "# This-turn surface: chat\n"
    "Prefer a direct answer. Tools are minimal (time / clarify / expand). "
    "If you need files, shell, or search, call use_tool_pack to enable packs "
    "before improvising missing tools."
)

SURFACE_SEARCH_GUIDANCE = (
    "# This-turn surface: search\n"
    "Use web_search / search / fetch as needed; budget 3–6 queries, then synthesize. "
    "Do not open files or run shell unless the user explicitly asked. "
    "Cite sources briefly when answering."
)

SURFACE_CODING_GUIDANCE = (
    "# This-turn surface: coding\n"
    "Path: read relevant files → edit/apply_patch → verify with command/python. "
    "Batch independent reads. Prefer unified diff style when presenting changes. "
    "Do not claim a tool is unavailable when it is in your tool list."
)

SURFACE_MCP_GUIDANCE = (
    "# This-turn surface: MCP ops\n"
    "Use manage_mcp (list / update env / reload). Call mcp_* only for live tool use. "
    "Do not web_search how to configure when the user already provided a key. "
    "If a key is missing, ask once for `name API Key：xxxx`."
)

# 运行模式提示词（native_reasoning=True 时用无 <thinking> 变体，防双通道思考）
MODE_PROMPTS = {
    "deepthink": (
        "# Deep Think Mode\n"
        "Analyze each question step by step in depth. Put reasoning in "
        "<thinking>...</thinking> tags, final conclusion outside.\n"
        "Process: 1) Decompose dimensions 2) Analyze possibilities and "
        "constraints 3) Reason and verify 4) Draw conclusion."
    ),
    "deepthink_native": (
        "# Deep Think Mode\n"
        "Analyze each question step by step in depth. Reasoning is streamed "
        "separately by the provider — keep the visible reply as the conclusion only.\n"
        "Process: 1) Decompose dimensions 2) Analyze possibilities and "
        "constraints 3) Reason and verify 4) Draw conclusion."
    ),
    "search": (
        "# Search Mode\n"
        "Proactively use web_search / search for current events and facts you "
        "are unsure about. Budget a few queries, then synthesize a clear answer. "
        "Cite sources. Avoid opening the full coding toolkit for pure research."
    ),
    "plan": (
        "# Plan Mode\n"
        "Produce a structured plan only — title, summary, steps, risks, verification. "
        "Do not write files or run destructive commands until the user approves "
        "(e.g. 「批准计划」 / approve plan). Prefer read-only tools if needed."
    ),
    "goal": (
        "# Goal Mode — Autonomous Task Execution\n"
        "You are executing a complex goal that may require multiple tool calls.\n"
        "1. Break the goal into an actionable todo list\n"
        "2. Advance 1-3 todos per turn; update status as you go\n"
        "3. Before responding, confirm all todos are done\n"
        "4. Do not stop until finished; if blocked, explain what you need\n"
        "5. Mid-work turns with tools: a short progress line in the user's language is enough\n"
        "6. When tools are disabled this turn, the goal completes, or a segment ends: "
        "write a **full user-facing summary** (done / remaining / evidence / next steps) "
        "— not an empty body and not a tool-call inventory dump\n"
        "7. Use ```mermaid for diagrams; fenced code blocks with language tags for code"
    ),
    "goal_native": (
        "# Goal Mode — Autonomous Task Execution\n"
        "You are executing a complex goal that may require multiple tool calls.\n"
        "1. Break the goal into an actionable todo list\n"
        "2. Advance 1-3 todos per turn; update status as you go\n"
        "3. Before responding, confirm all todos are done\n"
        "4. Do not stop until finished; if blocked, explain what you need\n"
        "5. Do **not** write <thinking> tags or restate chain-of-thought in the body\n"
        "6. Mid-work + tools: short progress line (user language) is fine\n"
        "7. Segment end / goal complete / tools disabled: full user-facing summary "
        "(done / remaining / checks / next steps); never leave the body empty\n"
        "8. Use ```mermaid for diagrams; fenced code blocks with language tags for code"
    ),
    "code": (
        "# Code Mode\n"
        "Focus on writing, reviewing, and debugging code. Give complete, "
        "runnable implementations — no placeholders. Test before declaring "
        "done. Follow existing project conventions and style. "
        "Keep visible narration short; do not dump multi-line status inventories."
    ),
}


# ═══════════════════════════════════════════════════════════════
# 组装函数
# ═══════════════════════════════════════════════════════════════

def build_system_prompt(
    *,
    # Stable 层参数
    identity: str | None = None,
    tools_enabled: list[str] | None = None,
    model: str | None = None,
    # Context 层参数
    user_system_prompt: str | None = None,
    context_files: str | None = None,
    platform: str | None = None,
    mode: str | None = None,
    # Volatile 层参数
    memory_block: str | None = None,
    session_id: str | None = None,
) -> dict[str, str]:
    """
    组装系统提示词为三层结构。

    Returns:
        {"stable": ..., "context": ..., "volatile": ...}
        调用方用 "\\n\\n" 合并为完整 system prompt。
    """
    # ── Stable 层 ──────────────────────────────────────────
    stable_parts: list[str] = []

    # 1. 身份（用户可覆盖，但底层有默认值）
    stable_parts.append(identity or DEFAULT_IDENTITY)
    # Reply language follows the user (no fixed locale)
    stable_parts.append(USER_LANGUAGE_RULE)

    # 2. 工具使用指导
    # tools_enabled is None = 调用方未传名单（默认仍有工具）→ 注入纪律
    # tools_enabled == [] = 明确无工具 → 不注入
    if tools_enabled is None:
        has_tools = True
        tool_set: set[str] = set()
        tools_known = False
    else:
        tool_set = set(tools_enabled)
        has_tools = bool(tool_set)
        tools_known = True

    if has_tools:
        stable_parts.append(TOOL_USE_ENFORCEMENT)
        stable_parts.append(TASK_COMPLETION)
        # 并行 / 搜索收敛：仅在工具面可能用到时注入，避免闲聊膨胀
        _codingish = bool(
            tool_set
            & {
                "file_read", "file_write", "edit", "apply_patch",
                "command", "python", "grep", "glob",
            }
        ) or not tools_known
        _searchish = bool(
            tool_set & {"web_search", "search", "fetch_webpage", "browser", "http"}
        ) or not tools_known
        if _codingish or _searchish or not tools_known:
            stable_parts.append(PARALLEL_TOOL_CALLS)
        if _searchish or not tools_known:
            stable_parts.append(SEARCH_CONVERGENCE)

        # Surface fence: match this-turn tool list (Grok-style thin schema)
        if tools_known:
            _mcp = "manage_mcp" in tool_set or any(
                str(t).startswith("mcp_") for t in tool_set
            )
            _chat_only = (
                not _codingish
                and not _searchish
                and not _mcp
                and tool_set <= {
                    "use_tool_pack", "current_time", "clarify", "session_search",
                    "doc_read", "list_available_models", "get_system_status",
                    "capability_status", "result_load",
                }
            )
            if _mcp and not _codingish:
                stable_parts.append(SURFACE_MCP_GUIDANCE)
            elif _chat_only:
                stable_parts.append(SURFACE_CHAT_GUIDANCE)
            elif _searchish and not _codingish:
                stable_parts.append(SURFACE_SEARCH_GUIDANCE)
            elif _codingish:
                stable_parts.append(SURFACE_CODING_GUIDANCE)

        if "memory" in tool_set or "memory_pref" in tool_set:
            stable_parts.append(MEMORY_GUIDANCE)

        # 技能短指导；Evolution 仅在已知工具集且含进化工具时注入
        stable_parts.append(SKILLS_GUIDANCE)
        evo_names = {"manage_evolution", "query_evolution", "manage_skill"}
        if tools_known and (
            tool_set & evo_names
            or any(n.startswith("evo_") or n.startswith("evo__") for n in tool_set)
        ):
            stable_parts.append(EVOLUTION_GUIDANCE)

        code_tools = {"command", "file_write", "file_read", "edit", "python", "patch", "apply_patch"}
        if (not tools_known) or (code_tools & tool_set):
            stable_parts.append(CODE_QUALITY)
            try:
                from backend.agent.decisive import decisive_coding_guidance
                stable_parts.append(decisive_coding_guidance())
            except Exception:
                pass
            # Short progress discipline (soft); hard gates still enforce
            try:
                from backend.core.config import settings as _st_pd

                if bool(getattr(_st_pd, "agent_progress_discipline_prompt", True)):
                    stable_parts.append(
                        "# Progress discipline\n"
                        "- On [Blocked]/Poll throttle: follow the NEXT menu only; "
                        "never retry the same blocked command.\n"
                        "- Background cargo: wait for [bg_complete]; do not spam process poll.\n"
                        "- Compile error[E…]: edit the --> path, then one cargo check.\n"
                        "- cwd: absolute under project workspace; never install-dir relative paths."
                    )
            except Exception:
                pass
        # 多类目落地纪律：审计/检索/数据/统计/文档/清单/排查/对比/计算…
        try:
            from backend.agent.task_grounding import grounding_prompt_block

            stable_parts.append(grounding_prompt_block())
        except Exception:
            pass

    # 3. 思考指导：原生 reasoning 模型不再教写 <thinking>（防双通道）
    _native_reason = False
    try:
        from backend.services.llm.reasoning_effort import supports_reasoning_control

        _native_reason = bool(supports_reasoning_control(model=model))
    except Exception:
        _native_reason = False
    # grok / o-series / reasoner 等：用 NATIVE 变体
    if _native_reason:
        stable_parts.append(THINKING_GUIDANCE_NATIVE)
    else:
        stable_parts.append(THINKING_GUIDANCE)

    # 4. 专业客观性（始终注入，防止过度讨好）
    stable_parts.append(PROFESSIONAL_OBJECTIVITY)

    # ── Context 层 ─────────────────────────────────────────
    context_parts: list[str] = []

    # 用户自定义系统提示词
    if user_system_prompt and user_system_prompt.strip():
        context_parts.append(user_system_prompt.strip())

    # 上下文文件（AGENTS.md 等）
    if context_files and context_files.strip():
        context_parts.append(context_files.strip())

    # 平台提示
    if platform and platform in PLATFORM_HINTS:
        context_parts.append(PLATFORM_HINTS[platform])

    # 模式提示（native 变体优先，避免 goal/deepthink 再要求手写 thinking 标签）
    if mode:
        mode_key = str(mode).strip().lower()
        if _native_reason and f"{mode_key}_native" in MODE_PROMPTS:
            context_parts.append(MODE_PROMPTS[f"{mode_key}_native"])
        elif mode_key in MODE_PROMPTS:
            context_parts.append(MODE_PROMPTS[mode_key])

    # ── Volatile 层 ────────────────────────────────────────
    volatile_parts: list[str] = []

    # 记忆快照
    if memory_block and memory_block.strip():
        volatile_parts.append(memory_block.strip())

    # 时间戳 + 会话信息（给 LLM 准确的双时区时间，避免回答「现在几点」时瞎猜）
    now_utc = tta_utc_now()
    now_local = tta_local_now()
    ts_line = (
        f"Current time: {now_local.strftime('%A, %B %d, %Y %H:%M:%S')} "
        f"({now_local.strftime('%Z')}) / {now_utc.strftime('%H:%M:%S')} UTC"
    )
    if session_id:
        ts_line += f"\nSession: {session_id[:8]}"
    if model:
        ts_line += f"\nModel: {model}"
    volatile_parts.append(ts_line)

    return {
        "stable": "\n\n".join(p for p in stable_parts if p and p.strip()),
        "context": "\n\n".join(p for p in context_parts if p and p.strip()),
        "volatile": "\n\n".join(p for p in volatile_parts if p and p.strip()),
    }


def merge_prompt_parts(parts: dict[str, str], *, include_volatile: bool = True) -> str:
    """将三层合并为完整 system prompt 字符串。

    include_volatile=False（prompt-cache 友好模式，见 T4）：
        只合并 stable+context。Volatile 层含**秒级时间戳**，若并入 messages[0]，
        每个新用户轮次 system 块都不同 —— Anthropic 的 system cache 与 OpenAI 的
        自动前缀缓存会在第一个 block 就失配，整段历史前缀缓存全部作废。
        调用方（ContextManager）改为把 volatile 放到 messages 尾部，
        既保住稳定前缀，又因更靠近当前问题而更容易被模型注意到。
    """
    ordered = [parts.get("stable", ""), parts.get("context", "")]
    if include_volatile:
        ordered.append(parts.get("volatile", ""))
    return "\n\n".join(p for p in ordered if p and p.strip())
