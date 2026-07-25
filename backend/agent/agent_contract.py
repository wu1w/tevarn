"""Agent 交付契约 + 复核循环（Phase 2：Agent Contract + Review Loop）

子代理产出不再是一坨无结构文本，而是带签名/置信度/关键断言的交付物
（AgentDeliverable）；独立的 reviewer LLM 对交付物复核（pass/revise/reject），
revise 触发有限次返工，reject 从综合输入中剔除。

诚实边界：
- signature 是**溯源指纹**（sha256 前 16 位），用于检测下游是否篡改交付内容，
  不是密码学身份认证。
- confidence 由子代理自评，仅供参考；复核分数由 reviewer 给出，同样仅供参考。
  两者都不保证正确性，只是可观测信号。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ─────────── 交付物契约 ───────────

CONTRACT_INSTRUCTION = (
    "\n\n---\n"
    "【交付格式要求】请把你的最终答案包在一个 JSON 代码块中返回，格式：\n"
    "```json\n"
    '{"content": "<你的完整回答（Markdown 可）>", '
    '"confidence": <0.0~1.0 的自评置信度>, '
    '"claims": ["<关键断言1>", "<关键断言2>"]}\n'
    "```\n"
    "confidence 诚实评估：不确定就低分。claims 列出回答中最关键、最可能出错的事实性断言。"
)

REVIEWER_SYSTEM_PROMPT = (
    "你是一个严格的交付物复核员（reviewer）。你的职责：\n"
    "1) 核对每个交付物是否真正回答了对应子任务；\n"
    "2) 检查关键断言是否有明显事实错误、自相矛盾或凭空捏造；\n"
    "3) 给出 verdict：pass（可用）/ revise（有小问题，需返工，附具体修改建议）/ "
    "reject（严重错误或答非所问，不可用）。\n"
    "只输出一个 JSON 数组，不要输出其他内容：\n"
    '[{"task_id": "...", "verdict": "pass|revise|reject", '
    '"score": 0.0~1.0, "issues": ["..."], "suggestion": "...或null"}]'
)


def sign_deliverable(content: str, agent_id: str, task_id: str, model: str | None) -> str:
    """溯源指纹：内容+来源的 sha256 前 16 位（防下游篡改，非身份认证）"""
    raw = f"{agent_id}|{task_id}|{model or 'default'}|{content}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class AgentDeliverable:
    """子代理交付物（契约化产出）"""

    agent_id: str
    task_id: str
    content: str
    confidence: float | None = None
    claims: list[str] = field(default_factory=list)
    model: str | None = None
    signature: str = ""

    def __post_init__(self) -> None:
        if not self.signature:
            self.signature = sign_deliverable(
                self.content, self.agent_id, self.task_id, self.model
            )

    def verify(self) -> bool:
        """校验内容是否被下游篡改"""
        return self.signature == sign_deliverable(
            self.content, self.agent_id, self.task_id, self.model
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "content": self.content,
            "confidence": self.confidence,
            "claims": list(self.claims),
            "model": self.model,
            "signature": self.signature,
        }


def _extract_json_block(text: str) -> str | None:
    """从文本中提取第一个 JSON 对象/数组（支持 ```json 围栏或裸 JSON）"""
    fence = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    # 裸 JSON：候选起点为 { 或 [（取更早者优先，失败再试另一个）
    starts = sorted(
        i for i in (text.find("{"), text.find("[")) if i != -1
    )
    for start in starts:
        # 从后往前尝试解析
        for end in range(len(text), start, -1):
            chunk = text[start:end].strip()
            if not chunk or chunk[-1] not in ("}", "]"):
                continue
            try:
                json.loads(chunk)
                return chunk
            except Exception:
                continue
    return None


def parse_deliverable(
    raw_text: str,
    *,
    agent_id: str,
    task_id: str,
    model: str | None = None,
) -> AgentDeliverable:
    """把子代理原始输出解析为契约交付物；不合规时降级（全文 content）"""
    text = (raw_text or "").strip()
    block = _extract_json_block(text)
    if block:
        try:
            data = json.loads(block)
            if isinstance(data, dict) and "content" in data:
                conf = data.get("confidence")
                try:
                    conf = float(conf) if conf is not None else None
                    if conf is not None:
                        conf = max(0.0, min(1.0, conf))
                except (TypeError, ValueError):
                    conf = None
                claims = data.get("claims")
                if not isinstance(claims, list):
                    claims = []
                return AgentDeliverable(
                    agent_id=agent_id,
                    task_id=task_id,
                    content=str(data.get("content") or "").strip() or text,
                    confidence=conf,
                    claims=[str(c) for c in claims if c],
                    model=model,
                )
        except Exception as e:
            logger.debug("deliverable JSON parse failed, fallback raw: %s", e)
    # 降级：不符合契约 → 全文作为 content，无置信度
    return AgentDeliverable(
        agent_id=agent_id, task_id=task_id, content=text, model=model
    )


# ─────────── 复核 ───────────


@dataclass
class ReviewVerdict:
    """reviewer 对单个交付物的复核结论"""

    task_id: str
    verdict: str  # pass | revise | reject
    score: float | None = None
    issues: list[str] = field(default_factory=list)
    suggestion: str | None = None
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "verdict": self.verdict,
            "score": self.score,
            "issues": list(self.issues),
            "suggestion": self.suggestion,
        }


def parse_review_verdicts(raw_text: str, task_ids: list[str]) -> dict[str, ReviewVerdict]:
    """解析 reviewer 输出（JSON 数组）；解析失败的 task 默认 pass（不误杀）"""
    text = (raw_text or "").strip()
    block = _extract_json_block(text)
    verdicts: dict[str, ReviewVerdict] = {}
    items: list[Any] = []
    if block:
        try:
            data = json.loads(block)
            items = data if isinstance(data, list) else [data]
        except Exception as e:
            logger.debug("review JSON parse failed: %s", e)
    if not items:
        # reviewer 输出不可解析 → 全部默认 pass（复核失败不应阻塞交付）
        for tid in task_ids:
            verdicts[tid] = ReviewVerdict(task_id=tid, verdict="pass", raw=text)
        return verdicts
    by_id: dict[str, dict] = {}
    for it in items:
        if isinstance(it, dict) and it.get("task_id"):
            by_id[str(it["task_id"])] = it
    for tid in task_ids:
        it = by_id.get(tid)
        if it is None:
            verdicts[tid] = ReviewVerdict(task_id=tid, verdict="pass", raw=text)
            continue
        v = str(it.get("verdict") or "pass").strip().lower()
        if v not in ("pass", "revise", "reject"):
            v = "pass"
        score = it.get("score")
        try:
            score = float(score) if score is not None else None
            if score is not None:
                score = max(0.0, min(1.0, score))
        except (TypeError, ValueError):
            score = None
        issues = it.get("issues")
        if not isinstance(issues, list):
            issues = []
        sugg = it.get("suggestion")
        verdicts[tid] = ReviewVerdict(
            task_id=tid,
            verdict=v,
            score=score,
            issues=[str(x) for x in issues if x],
            suggestion=str(sugg) if sugg else None,
            raw=text,
        )
    return verdicts


def build_review_prompt(task_description: str, deliverables: list[AgentDeliverable]) -> str:
    """构造复核请求（原始任务 + 全部交付物）"""
    payload = [
        {
            "task_id": d.task_id,
            "agent_id": d.agent_id,
            "content": d.content,
            "confidence": d.confidence,
            "claims": d.claims,
        }
        for d in deliverables
    ]
    return (
        f"【原始任务】\n{task_description}\n\n"
        f"【待复核交付物】\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "请逐个复核并输出 JSON 数组。"
    )
