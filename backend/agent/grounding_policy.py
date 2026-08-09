"""Grounding intensity policy — balance hallucination safety vs model freedom.

Strong models (Claude / GPT / Grok / Kimi / GLM / …) degrade when the harness
over-forces tool rituals and floods the system prompt. Weak models still need
a light safety net. Default is **soft**:

| Mode       | Hard force-followup              | Assign hard-block              | Prompt bulk |
|------------|----------------------------------|--------------------------------|-------------|
| soft       | empty tools once; fix/build write| phantom paths / empty only     | short       |
| balanced   | + list-only / wrong-bucket once  | + template modules             | short       |
| strict     | full multi-category budgets      | multi-class poison             | medium      |

Env: ``TEVARN_GROUNDING_MODE=soft|balanced|strict`` (default soft).
Optional model name auto-upgrades strict→balanced and balanced→soft for
known strong families (never upgrades *into* stricter).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

Mode = Literal["soft", "balanced", "strict"]

_STRONG = re.compile(
    r"(claude|sonnet|opus|haiku|gpt-?4|gpt-?5|o1|o3|o4|"
    r"grok|kimi|moonshot|glm|chatglm|gemini|deepseek|"
    r"qwen|codex|mistral-large|command-r)",
    re.I,
)
_WEAK = re.compile(
    r"(tiny|mini|nano|1b|3b|7b|8b|small|lite|flash-lite|haiku-?3)",
    re.I,
)


@dataclass(frozen=True)
class GroundingPolicy:
    mode: Mode
    model_tier: str  # strong | default | weak

    # completion / evaluate_grounding
    hard_empty_tools: bool = True
    hard_fix_build_write: bool = True
    hard_list_only: bool = False
    hard_wrong_bucket: bool = False
    hard_shallow: bool = False
    hard_few_deep: bool = False
    hard_long_report: bool = False
    hard_certainty_language: bool = False
    hard_dispatch_without_evidence: bool = False
    max_hard_followups: int = 1

    # assign / scan_dispatch
    block_missing_paths: bool = True
    block_template_modules: bool = True
    block_hard_metrics: bool = False
    block_stack_traces: bool = False
    block_multi_cve: bool = False
    block_latest_certain_combo: bool = False

    # prompt / hygiene bulk
    short_prompts: bool = True


def _raw_mode() -> Mode:
    raw = (os.environ.get("TEVARN_GROUNDING_MODE") or "soft").strip().lower()
    if raw in ("soft", "balanced", "strict", "hard"):
        return "strict" if raw == "hard" else raw  # type: ignore[return-value]
    return "soft"


def classify_model_tier(model_name: str | None) -> str:
    name = (model_name or "").strip()
    if not name:
        return "default"
    if _WEAK.search(name) and not _STRONG.search(name):
        return "weak"
    if _STRONG.search(name):
        return "strong"
    return "default"


def resolve_mode(model_name: str | None = None) -> Mode:
    """Env mode, relaxed one step for strong models (never tightened)."""
    mode = _raw_mode()
    tier = classify_model_tier(model_name)
    if tier == "strong":
        if mode == "strict":
            return "balanced"
        if mode == "balanced":
            return "soft"
    if tier == "weak" and mode == "soft":
        return "balanced"  # weak models get a slightly firmer net
    return mode


def build_policy(model_name: str | None = None) -> GroundingPolicy:
    mode = resolve_mode(model_name)
    tier = classify_model_tier(model_name)

    if mode == "strict":
        return GroundingPolicy(
            mode=mode,
            model_tier=tier,
            hard_empty_tools=True,
            hard_fix_build_write=True,
            hard_list_only=True,
            hard_wrong_bucket=True,
            hard_shallow=True,
            hard_few_deep=True,
            hard_long_report=True,
            hard_certainty_language=True,
            hard_dispatch_without_evidence=True,
            max_hard_followups=3,
            block_missing_paths=True,
            block_template_modules=True,
            block_hard_metrics=True,
            block_stack_traces=True,
            block_multi_cve=True,
            block_latest_certain_combo=True,
            short_prompts=False,
        )
    if mode == "balanced":
        return GroundingPolicy(
            mode=mode,
            model_tier=tier,
            hard_empty_tools=True,
            hard_fix_build_write=True,
            hard_list_only=True,
            hard_wrong_bucket=False,
            hard_shallow=False,
            hard_few_deep=False,
            hard_long_report=False,
            hard_certainty_language=False,
            hard_dispatch_without_evidence=False,  # soft nudge only via assign warn
            max_hard_followups=2,
            block_missing_paths=True,
            block_template_modules=True,
            block_hard_metrics=False,
            block_stack_traces=False,
            block_multi_cve=False,
            block_latest_certain_combo=False,
            short_prompts=True,
        )
    # soft (default)
    return GroundingPolicy(
        mode="soft",
        model_tier=tier,
        hard_empty_tools=True,
        hard_fix_build_write=True,
        hard_list_only=False,
        hard_wrong_bucket=False,
        hard_shallow=False,
        hard_few_deep=False,
        hard_long_report=False,
        hard_certainty_language=False,
        hard_dispatch_without_evidence=False,
        max_hard_followups=1,
        block_missing_paths=True,
        block_template_modules=True,  # still high-poison for workers
        block_hard_metrics=False,
        block_stack_traces=False,
        block_multi_cve=False,
        block_latest_certain_combo=False,
        short_prompts=True,
    )


@lru_cache(maxsize=8)
def get_policy(model_name: str | None = None) -> GroundingPolicy:
    return build_policy(model_name)


def clear_policy_cache() -> None:
    get_policy.cache_clear()
