"""Normalize provider-specific usage dicts into a unified shape.

Unified keys:
  prompt_tokens, completion_tokens, total_tokens,
  cache_read_input_tokens, cache_creation_input_tokens,
  billable_input_tokens, billable_tokens
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _i(v: Any) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return max(0, n)


def _details(raw: dict[str, Any], *keys: str) -> dict[str, Any]:
    for k in keys:
        d = raw.get(k)
        if isinstance(d, dict):
            return d
    return {}


def normalize_usage(
    raw: dict[str, Any] | None,
    *,
    family: str | None = None,
) -> dict[str, int]:
    """Map any known provider usage payload to unified int fields.

    Missing fields become 0 (omitted keys only if everything empty → {}).
    """
    if not isinstance(raw, dict) or not raw:
        return {}

    fam = (family or "").lower()
    prompt = 0
    completion = 0
    total = 0
    cache_read = 0
    cache_write = 0

    # ── Anthropic-native ──
    if fam == "anthropic" or (
        "input_tokens" in raw and "output_tokens" in raw and "prompt_tokens" not in raw
    ):
        inp = _i(raw.get("input_tokens"))
        cache_read = _i(raw.get("cache_read_input_tokens"))
        cache_write = _i(raw.get("cache_creation_input_tokens"))
        # Anthropic: input_tokens excludes cache hit; total prefix = sum
        prompt = inp + cache_read + cache_write if (inp or cache_read or cache_write) else 0
        completion = _i(raw.get("output_tokens"))
        total = prompt + completion if (prompt or completion) else 0
    else:
        # OpenAI-compatible base
        prompt = _i(raw.get("prompt_tokens") or raw.get("input_tokens"))
        completion = _i(
            raw.get("completion_tokens")
            or raw.get("output_tokens")
            or raw.get("completion_token_count")
        )
        total = _i(raw.get("total_tokens"))
        if not total and (prompt or completion):
            total = prompt + completion

        # Nested details (OpenAI / GLM / MiniMax / others)
        pdet = _details(raw, "prompt_tokens_details", "input_tokens_details")
        if pdet:
            cache_read = max(
                cache_read,
                _i(pdet.get("cached_tokens")),
                _i(pdet.get("cache_read_tokens")),
                _i(pdet.get("cache_read_input_tokens")),
            )

        # Top-level aliases (hit ≠ write; miss is uncached input, not creation)
        cache_read = max(
            cache_read,
            _i(raw.get("cache_read_input_tokens")),
            _i(raw.get("cached_tokens")),
            _i(raw.get("prompt_cache_hit_tokens")),
            _i(raw.get("cache_hit_tokens")),
        )
        cache_write = max(
            cache_write,
            _i(raw.get("cache_creation_input_tokens")),
            _i(raw.get("cache_write_tokens")),
            _i(raw.get("cache_write_input_tokens")),
        )

        # DeepSeek-style: hit + miss ≈ prompt
        hit = _i(raw.get("prompt_cache_hit_tokens"))
        miss = _i(raw.get("prompt_cache_miss_tokens")) or _i(raw.get("cache_miss_tokens"))
        if hit or miss:
            cache_read = max(cache_read, hit)
            if not prompt:
                prompt = hit + miss

    # Billable input: uncached portion + optional write cost weight
    # Prefer explicit miss when present
    miss_explicit = _i(raw.get("prompt_cache_miss_tokens")) or _i(raw.get("cache_miss_tokens"))
    if miss_explicit:
        billable_in = miss_explicit
    elif prompt > 0 and cache_read > 0:
        billable_in = max(0, prompt - cache_read)
    else:
        billable_in = prompt

    # Cache write often billed at ~1.25x; count write tokens into billable lightly
    # (full write tokens already in billable_in when they were "miss"; only add
    # creation when Anthropic-style separate write exists and wasn't in miss)
    if cache_write and fam == "anthropic":
        # Anthropic: write is separate from input_tokens in some reports;
        # billable ≈ input (non-cache) + write (we already folded write into prompt)
        # Use non-cached input + write as billable approximation:
        non_cache = max(0, prompt - cache_read - cache_write)
        billable_in = non_cache + cache_write

    billable = billable_in + completion

    out: dict[str, int] = {}
    if prompt or completion or cache_read or cache_write:
        out["prompt_tokens"] = prompt
        out["completion_tokens"] = completion
        out["total_tokens"] = total or (prompt + completion)
        out["cache_read_input_tokens"] = cache_read
        out["cache_creation_input_tokens"] = cache_write
        out["billable_input_tokens"] = billable_in
        out["billable_tokens"] = billable
    return out


def log_cache_usage(model: str, usage: dict[str, int], *, family: str = "") -> None:
    total = int(usage.get("prompt_tokens") or 0)
    if total <= 0:
        return
    read = int(usage.get("cache_read_input_tokens") or 0)
    write = int(usage.get("cache_creation_input_tokens") or 0)
    bill = int(usage.get("billable_tokens") or 0)
    logger.info(
        "prompt cache family=%s model=%s prompt=%s cache_read=%s (%.0f%%) cache_write=%s billable=%s",
        family or "?",
        model,
        total,
        read,
        100.0 * read / total if total else 0.0,
        write,
        bill,
    )
    # P0.5 R1: aggregate into Rust cache_metrics (hit if any cache_read)
    _report_cache_to_kernel(
        family=family or "default",
        model=model or "",
        hit=read > 0,
        bytes_saved=max(0, read) * 4,  # rough chars≈tokens*4
    )


def _report_cache_to_kernel(
    *, family: str, hit: bool, bytes_saved: int = 0, model: str = ""
) -> None:
    try:
        from backend.kernel import get_kernel

        k = get_kernel()
        fam = (family or "default").strip() or "default"
        mid = (model or "").strip()
        if hasattr(k, "cache_record"):
            try:
                k.cache_record(
                    fam, hit=hit, bytes_saved=int(bytes_saved or 0), model=mid or None
                )
            except TypeError:
                k.cache_record(fam, hit=hit, bytes_saved=int(bytes_saved or 0))
        elif hasattr(k, "_call"):
            payload: dict[str, Any] = {
                "family": fam,
                "hit": bool(hit),
                "bytes_saved": int(bytes_saved or 0),
            }
            if mid:
                payload["model"] = mid
            k._call("cache_record", payload)
    except Exception as e:
        logger.debug("cache_record skip: %s", e)


def report_cost_to_kernel(
    *,
    process_id: str | None,
    family: str,
    tokens: int,
    billable: int,
    model: str | None = None,
) -> None:
    """P0.5 R5: charge 3D cost ledger (tokens / billable), keyed by family+model."""
    pid = (process_id or "").strip() or "system"
    try:
        from backend.kernel import get_kernel

        k = get_kernel()
        fam = (family or "default").strip() or "default"
        mid = (model or "").strip()
        params: dict[str, Any] = {
            "process_id": pid,
            "family": fam,
            "tokens": max(0, int(tokens or 0)),
            "billable": max(0, int(billable or 0)),
        }
        if mid:
            params["model"] = mid
        if hasattr(k, "cost_charge"):
            try:
                k.cost_charge(
                    pid,
                    fam,
                    params["tokens"],
                    params["billable"],
                    model=mid or None,
                )
            except TypeError:
                k.cost_charge(pid, fam, params["tokens"], params["billable"])
        elif hasattr(k, "_call"):
            k._call("cost_charge", params)
    except Exception as e:
        logger.debug("cost_charge skip: %s", e)


def charge_amount_from_usage(
    usage: dict[str, int] | None,
    *,
    prefer_billable: bool = True,
    fallback: int = 0,
) -> int:
    """Token amount to charge Kernel / daily quota for one LLM round."""
    if not isinstance(usage, dict) or not usage:
        return max(0, int(fallback or 0))
    if prefer_billable:
        b = _i(usage.get("billable_tokens"))
        if b > 0:
            return b
        bi = _i(usage.get("billable_input_tokens"))
        c = _i(usage.get("completion_tokens"))
        if bi or c:
            return bi + c
    p = _i(usage.get("prompt_tokens"))
    c = _i(usage.get("completion_tokens"))
    if p or c:
        return p + c
    return max(0, int(fallback or 0))
