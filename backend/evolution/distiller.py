"""Trajectory Distiller —— 从成功/失败轨迹蒸馏 SKILL.md（P1，2026-07-29 夜间路线图）。

与 improver.py 的分工：
- improver：失败驱动、模板拼接（无 LLM 依赖，永远可用的兜底）
- distiller：成功经验也沉淀（Hermes 式 learning loop），LLM 蒸馏出
  agentskills.io 兼容的 SKILL.md；LLM 不可用时回落 improver 模板

治理红线（与现有 evolution 链一致）：
- 产物只进 evolution store（status=draft），走既有 审批→apply→rollback 链
- 绝不直接写 skills 表 / 注册 runtime tool——那是 skill_sync 在 approve 后做的事
- 蒸馏失败静默降级，绝不打断主循环（与 RunRecorder 同一契约）
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 蒸馏门槛：轨迹太短没有可复用流程可言
MIN_TOOL_CALLS = 3
# 单个轨迹注入 LLM 的最大字符（防止长轨迹撑爆上下文）
MAX_TRACE_CHARS = 6000

_DISTILL_SYSTEM = """你是一个 agent 技能蒸馏器。输入是一次 agent 任务的执行轨迹（用户意图 + 工具调用序列 + 最终结果）。
你的任务：判断这条轨迹是否包含「可复用的流程知识」，如果有，蒸馏成一份 SKILL.md。

严格要求：
1. 只在轨迹展现了可泛化流程时输出技能；一次性查询/闲聊类轨迹输出 {"skip": true, "reason": "..."}
2. 技能内容必须去除本次任务的具体参数（文件名/URL/数字），保留方法论
3. 输出 JSON：{"skip": false, "name": "<ascii_snake_case>", "description": "<一句话，何时用>", "body": "<markdown 正文>"}
4. body 结构：## 适用场景 / ## 步骤 / ## 验证方式 / ## 常见陷阱（陷阱来自轨迹中真实发生的弯路）
5. 全程中文（name 除外），不要输出 JSON 以外的任何内容"""


def _clip_trace(tool_trace: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for i, t in enumerate(tool_trace or []):
        name = t.get("name") or "?"
        args = str(t.get("args") or t.get("arguments") or "")[:200]
        result = str(t.get("result") or "")[:300]
        lines.append(f"{i + 1}. {name}({args}) -> {result}")
    text = "\n".join(lines)
    if len(text) > MAX_TRACE_CHARS:
        # 保头保尾：开头的探索和结尾的收敛最有信息量
        head = text[: MAX_TRACE_CHARS // 2]
        tail = text[-MAX_TRACE_CHARS // 2 :]
        text = head + "\n...(中间截断)...\n" + tail
    return text


def render_skill_md(name: str, description: str, body: str, meta: dict[str, Any] | None = None) -> str:
    """agentskills.io / Claude Code 兼容的 SKILL.md（frontmatter: name + description）。"""
    m = meta or {}
    fm = [
        "---",
        f"name: {name}",
        f"description: {description}",
    ]
    if m.get("version"):
        fm.append(f"version: {m['version']}")
    fm.extend(
        [
            "source: takton-evolution",
            f"distilled_from: {m.get('distilled_from', 'trajectory')}",
            "---",
            "",
        ]
    )
    return "\n".join(fm) + body.strip() + "\n"


def _parse_llm_json(text: str) -> dict[str, Any] | None:
    """宽容解析：剥 markdown 代码围栏、找第一个 { 到最后一个 }。"""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.MULTILINE).strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(t[start : end + 1])
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _sanitize_skill_name(raw: str, fallback: str = "distilled_skill") -> str:
    name = re.sub(r"[^a-z0-9_]+", "_", (raw or "").strip().lower()).strip("_")
    return (name or fallback)[:64]


async def distill_from_trajectory(
    *,
    user_input: str,
    tool_trace: list[dict[str, Any]] | None,
    final_content: str = "",
    success: bool = True,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    """蒸馏一条轨迹。返回创建的 evolution asset dict；跳过/失败返回 None。

    调用方（loop epilogue / dispatcher 工单完成钩子）不需要 try/except——
    本函数吞掉一切异常，最坏返回 None。
    """
    try:
        trace = tool_trace or []
        if len(trace) < MIN_TOOL_CALLS:
            return None

        from backend.core.config import settings

        if not bool(getattr(settings, "agent_evolution_distill_enabled", True)):
            return None

        proposal = await _distill_llm(user_input, trace, final_content, success)
        if proposal is None:
            # LLM 不可用/拒绝输出 → 失败轨迹回落模板兜底；成功轨迹不硬造
            if success:
                return None
            proposal = _distill_template_fallback(user_input, trace, final_content)
        if proposal is None:
            return None

        return _store_draft(proposal, session_id=session_id, success=success)
    except Exception as e:
        logger.warning("distill_from_trajectory swallowed: %s", e)
        return None


async def _distill_llm(
    user_input: str,
    trace: list[dict[str, Any]],
    final_content: str,
    success: bool,
) -> dict[str, Any] | None:
    try:
        from backend.services.llm import LLMServiceFactory

        service = LLMServiceFactory.get_service()
        outcome = "成功" if success else "失败"
        user_msg = (
            f"任务意图：{(user_input or '')[:400]}\n"
            f"结局：{outcome}\n"
            f"最终输出（截断）：{(final_content or '')[:400]}\n\n"
            f"工具轨迹：\n{_clip_trace(trace)}"
        )
        resp = await service.chat_complete(
            messages=[
                {"role": "system", "content": _DISTILL_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
        )
        content = getattr(resp, "content", None) or ""
        obj = _parse_llm_json(content)
        if not obj or obj.get("skip"):
            return None
        name = _sanitize_skill_name(str(obj.get("name") or ""))
        description = str(obj.get("description") or "").strip()[:280]
        body = str(obj.get("body") or "").strip()
        if not name or not description or len(body) < 80:
            return None
        return {"name": name, "description": description, "body": body, "generator": "llm"}
    except Exception as e:
        logger.debug("distill LLM path unavailable: %s", e)
        return None


def _distill_template_fallback(
    user_input: str,
    trace: list[dict[str, Any]],
    final_content: str,
) -> dict[str, Any] | None:
    """LLM 不可用时复用 improver 的失败驱动模板。"""
    try:
        from backend.evolution.improver import (
            classify_failures,
            propose_skill_from_failure,
        )

        codes = classify_failures(tool_trace=trace, final_content=final_content)
        if not codes:
            return None
        p = propose_skill_from_failure(
            user_input=user_input,
            failure_codes=codes,
            tool_trace=trace,
            final_content=final_content,
            source_label="distill_fallback",
        )
        return {
            "name": p["name"],
            "description": p["summary"][:280],
            "body": p["content"],
            "generator": "template",
        }
    except Exception as e:
        logger.debug("distill template fallback failed: %s", e)
        return None


def _store_draft(
    proposal: dict[str, Any],
    *,
    session_id: str | None,
    success: bool,
) -> dict[str, Any] | None:
    """draft 入库（去重：同名已有非归档资产则跳过，交给 scoreboard 迭代）。"""
    from backend.evolution import store

    name = proposal["name"]
    for a in store.list_assets(kind="skill", limit=200):
        if a.get("name") == name and a.get("status") not in ("archived", "rejected"):
            logger.debug("distill dedup: asset %s already exists (status=%s)", name, a.get("status"))
            return None

    skill_md = render_skill_md(
        name,
        proposal["description"],
        proposal["body"],
        meta={"distilled_from": "success_trajectory" if success else "failure_trajectory"},
    )
    meta = {
        "format": "skill_md_agentskills_v1",
        "generator": proposal.get("generator", "llm"),
        "from_success": success,
    }
    # Phase 4.1：保留轨迹摘要供回放验证
    if proposal.get("tool_trace"):
        meta["tool_trace"] = proposal["tool_trace"][:80]
    if proposal.get("baseline_metrics"):
        meta["baseline_metrics"] = proposal["baseline_metrics"]

    asset = store.create_asset(
        kind="skill",
        name=name,
        summary=proposal["description"],
        content=skill_md,
        source="auto",
        status="draft",
        session_id=session_id,
        meta=meta,
    )
    # 入库后立即跑回放验证，审批面板可直接看 pass/fail
    try:
        from backend.evolution.replay_validator import validate_and_attach

        if asset and asset.get("id"):
            validate_and_attach(str(asset["id"]))
            asset = store.get_asset(str(asset["id"])) or asset
    except Exception as e:
        logger.debug("replay attach after distill skipped: %s", e)
    logger.info("distilled skill draft created: %s (gen=%s)", name, asset.get("gen") if asset else "?")
    return asset
