"""Normalize provider-specific usage dicts into a unified shape.

Unified keys:
  prompt_tokens, completion_tokens, total_tokens,
  cache_read_input_tokens, cache_creation_input_tokens,
  billable_input_tokens, billable_tokens,
  usage_source  (int flag: 1=provider real, 0=estimated — optional)

Accuracy rules for compression / cost optimization:
  - Prefer provider-reported numbers; never invent cache hits.
  - Anthropic: input_tokens excludes cache; total prompt = input+read+write.
  - OpenAI / Responses / compat: prompt/input includes cached; details.cached_tokens.
  - Record cost + cache once per LLM round (call record_round_usage from llm_round).
  - Provider adapters must only normalize — do not dual-write ledgers.
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


def _is_anthropic_family(family: str | None) -> bool:
    fam = (family or "").strip().lower()
    if not fam:
        return False
    if fam == "anthropic" or fam.startswith("anthropic") or fam.startswith("claude"):
        return True
    return False


def merge_usage(dst: dict[str, int], src: dict[str, Any] | None) -> dict[str, int]:
    """Merge partial stream usage (e.g. Anthropic message_start + message_delta).

    Token component fields take max(prev, new). Billable is recomputed by
    finalize_usage() at round end — do not trust mid-stream billable alone.
    """
    if not isinstance(src, dict) or not src:
        return dst
    for k, v in src.items():
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n < 0:
            n = 0
        prev = int(dst.get(k) or 0)
        if k == "usage_source":
            # prefer provider-real (1)
            dst[k] = max(prev, n)
            continue
        # Prefer higher for partial streams (output grows; prompt usually fixed)
        dst[k] = max(prev, n) if (prev or n) else 0
    return dst


def finalize_usage(
    usage: dict[str, int] | None,
    *,
    family: str | None = None,
) -> dict[str, int]:
    """Recompute totals/billable from prompt/completion/cache components."""
    if not isinstance(usage, dict) or not usage:
        return {}
    u = dict(usage)
    prompt = _i(u.get("prompt_tokens"))
    completion = _i(u.get("completion_tokens"))
    cache_read = _i(u.get("cache_read_input_tokens"))
    cache_write = _i(u.get("cache_creation_input_tokens"))
    if prompt > 0 and cache_read > prompt:
        cache_read = prompt
    if not prompt and not completion and not cache_read and not cache_write:
        return u

    if _is_anthropic_family(family):
        non_cache = max(0, prompt - cache_read - cache_write)
        billable_in = non_cache + cache_write
    else:
        billable_in = max(0, prompt - cache_read) if prompt else _i(
            u.get("billable_input_tokens")
        )

    u["prompt_tokens"] = prompt
    u["completion_tokens"] = completion
    u["cache_read_input_tokens"] = cache_read
    u["cache_creation_input_tokens"] = cache_write
    u["billable_input_tokens"] = billable_in
    u["billable_tokens"] = billable_in + completion
    u["total_tokens"] = max(_i(u.get("total_tokens")), prompt + completion)
    if "usage_source" not in u:
        u["usage_source"] = 1
    return u


def normalize_usage(
    raw: dict[str, Any] | None,
    *,
    family: str | None = None,
) -> dict[str, int]:
    """Map any known provider usage payload to unified int fields.

    Missing fields become 0. Empty / unusable raw → {}.
    """
    if not isinstance(raw, dict) or not raw:
        return {}

    fam = (family or "").strip()
    cache_read = 0
    cache_write = 0

    # ── extract cache from all known shapes first ──
    pdet = _details(raw, "prompt_tokens_details", "input_tokens_details")
    if pdet:
        cache_read = max(
            cache_read,
            _i(pdet.get("cached_tokens")),
            _i(pdet.get("cache_read_tokens")),
            _i(pdet.get("cache_read_input_tokens")),
        )
        cache_write = max(
            cache_write,
            _i(pdet.get("cache_creation_input_tokens")),
            _i(pdet.get("cache_write_tokens")),
            _i(pdet.get("cache_write_input_tokens")),
        )
    # output_tokens_details: no cache fields used today
    _ = _details(raw, "output_tokens_details", "completion_tokens_details")

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

    # DeepSeek / some gateways
    hit = _i(raw.get("prompt_cache_hit_tokens"))
    miss = _i(raw.get("prompt_cache_miss_tokens")) or _i(raw.get("cache_miss_tokens"))
    if hit:
        cache_read = max(cache_read, hit)

    prompt = 0
    completion = 0
    total = 0

    if _is_anthropic_family(fam):
        # Anthropic-native: input_tokens excludes cache hit (+ often excludes write)
        inp = _i(raw.get("input_tokens"))
        cache_read = max(cache_read, _i(raw.get("cache_read_input_tokens")))
        cache_write = max(cache_write, _i(raw.get("cache_creation_input_tokens")))
        prompt = (
            inp + cache_read + cache_write if (inp or cache_read or cache_write) else 0
        )
        completion = _i(raw.get("output_tokens"))
        total = prompt + completion if (prompt or completion) else 0
    else:
        # OpenAI-compatible + Responses API (input_tokens ≈ full prompt incl. cache)
        prompt = _i(raw.get("prompt_tokens") or raw.get("input_tokens"))
        completion = _i(
            raw.get("completion_tokens")
            or raw.get("output_tokens")
            or raw.get("completion_token_count")
        )
        total = _i(raw.get("total_tokens"))
        if not total and (prompt or completion):
            total = prompt + completion
        if hit or miss:
            if not prompt:
                prompt = hit + miss
            cache_read = max(cache_read, hit)

    # Clamp: cache_read cannot exceed prompt when prompt known
    if prompt > 0 and cache_read > prompt:
        cache_read = prompt

    # Billable input
    miss_explicit = _i(raw.get("prompt_cache_miss_tokens")) or _i(
        raw.get("cache_miss_tokens")
    )
    if miss_explicit:
        billable_in = miss_explicit
    elif prompt > 0 and cache_read > 0:
        billable_in = max(0, prompt - cache_read)
    else:
        billable_in = prompt

    if cache_write and _is_anthropic_family(fam):
        # Anthropic: non-cache input + write ≈ billable input approximation
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
        out["usage_source"] = 1  # provider-reported
    return out


def map_responses_usage_to_openai(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Map OpenAI Responses API usage → chat.completions-like dict for normalize."""
    if not isinstance(raw, dict) or not raw:
        return {}
    # Already OpenAI chat shape
    if "prompt_tokens" in raw or "completion_tokens" in raw:
        return dict(raw)
    out: dict[str, Any] = dict(raw)
    if "input_tokens" in raw and "prompt_tokens" not in raw:
        out["prompt_tokens"] = _i(raw.get("input_tokens"))
    if "output_tokens" in raw and "completion_tokens" not in raw:
        out["completion_tokens"] = _i(raw.get("output_tokens"))
    if "total_tokens" not in out:
        p = _i(out.get("prompt_tokens") or out.get("input_tokens"))
        c = _i(out.get("completion_tokens") or out.get("output_tokens"))
        if p or c:
            out["total_tokens"] = p + c
    # hoist nested cached_tokens into prompt_tokens_details for normalize
    idet = raw.get("input_tokens_details")
    if isinstance(idet, dict):
        out["input_tokens_details"] = idet
        out["prompt_tokens_details"] = {
            **(out.get("prompt_tokens_details") if isinstance(out.get("prompt_tokens_details"), dict) else {}),
            **idet,
        }
    return out


def resolve_usage_family(
    llm_service: Any = None,
    *,
    model: str | None = None,
    provider_id: str | None = None,
    settings: Any = None,
) -> tuple[str, str]:
    """Single attribution path for cost + cache (family, model).

    Priority: service.provider_id → service._family() → settings catalog → model hint.
    """
    mid = (model or "").strip()
    fam = (provider_id or "").strip()

    if llm_service is not None:
        if not mid:
            mid = str(getattr(llm_service, "model", None) or "").strip()
        if not fam:
            fam = str(getattr(llm_service, "provider_id", None) or "").strip()
        generic = {
            "",
            "custom",
            "generic",
            "openai-compatible",
            "openai_compatible",
            "default",
        }
        if fam.lower() in generic and hasattr(llm_service, "_family"):
            try:
                fam = str(llm_service._family() or "").strip()
            except Exception:
                pass

    generic = {
        "",
        "custom",
        "generic",
        "openai-compatible",
        "openai_compatible",
        "default",
    }
    if fam.lower() in generic and settings is not None:
        fam = str(
            getattr(settings, "llm_catalog_provider_id", None)
            or getattr(settings, "llm_provider", None)
            or fam
            or "default"
        ).strip()

    if fam.lower() in generic and mid:
        try:
            from backend.services.llm.provider_profiles import _family_from_model

            hint = _family_from_model(mid)
            if hint:
                fam = str(hint).strip()
        except Exception:
            pass

    if not fam:
        fam = "default"
    if not mid and settings is not None:
        mid = str(getattr(settings, "llm_model", None) or "").strip()
    return fam, mid


def log_cache_usage(model: str, usage: dict[str, int], *, family: str = "") -> None:
    """Log + durable/kernel cache sample.

    Prefer record_round_usage (once per round). This remains for tests / legacy.
    """
    total = int(usage.get("prompt_tokens") or 0)
    if total <= 0 and not int(usage.get("cache_read_input_tokens") or 0):
        return
    read = int(usage.get("cache_read_input_tokens") or 0)
    write = int(usage.get("cache_creation_input_tokens") or 0)
    bill = int(usage.get("billable_tokens") or 0)
    logger.info(
        "prompt cache family=%s model=%s prompt=%s cache_read=%s (%.1f%%) cache_write=%s billable=%s",
        family or "?",
        model,
        total,
        read,
        100.0 * read / total if total else 0.0,
        write,
        bill,
    )
    _report_cache_to_kernel(
        family=family or "default",
        model=model or "",
        hit=read > 0,
        bytes_saved=max(0, read) * 4,
        prompt_tokens=total,
        cache_read_tokens=read,
    )


def _report_cache_to_kernel(
    *,
    family: str,
    hit: bool,
    bytes_saved: int = 0,
    model: str = "",
    prompt_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> None:
    fam = (family or "default").strip() or "default"
    mid = (model or "").strip()
    try:
        from backend.services.usage_ledger import cache_record as durable_cache

        durable_cache(
            family=fam,
            hit=bool(hit),
            bytes_saved=int(bytes_saved or 0),
            model=mid or None,
            prompt_tokens=int(prompt_tokens or 0),
            cache_read_tokens=int(cache_read_tokens or 0),
        )
    except Exception as e:
        logger.warning("durable cache_record failed: %s", e)

    try:
        from backend.kernel import get_kernel

        k = get_kernel()
        if hasattr(k, "cache_record"):
            try:
                k.cache_record(
                    fam,
                    hit=hit,
                    bytes_saved=int(bytes_saved or 0),
                    model=mid or None,
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
        logger.debug("kernel cache_record skip: %s", e)


def report_cost_to_kernel(
    *,
    process_id: str | None,
    family: str,
    tokens: int,
    billable: int,
    model: str | None = None,
    prompt: int = 0,
    completion: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
    estimated: bool = False,
) -> None:
    """Charge cost ledger (tokens / billable / breakdown) by family+model."""
    pid = (process_id or "").strip() or "system"
    fam = (family or "default").strip() or "default"
    mid = (model or "").strip()
    tok = max(0, int(tokens or 0))
    bill = max(0, int(billable or 0))

    try:
        from backend.services.usage_ledger import charge as durable_charge

        durable_charge(
            process_id=pid,
            family=fam,
            tokens=tok,
            billable=bill,
            model=mid or None,
            prompt=int(prompt or 0),
            completion=int(completion or 0),
            cache_read=int(cache_read or 0),
            cache_write=int(cache_write or 0),
            estimated=bool(estimated),
        )
    except Exception as e:
        logger.warning("durable cost_charge failed: %s", e)

    try:
        from backend.kernel import get_kernel

        k = get_kernel()
        params: dict[str, Any] = {
            "process_id": pid,
            "family": fam,
            "tokens": tok,
            "billable": bill,
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
        logger.warning("kernel cost_charge skip (durable kept): %s", e)


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


def record_round_usage(
    *,
    usage: dict[str, int] | None,
    llm_service: Any = None,
    process_id: str | None = None,
    model: str | None = None,
    provider_id: str | None = None,
    settings: Any = None,
    estimated_tokens: int = 0,
    estimated_billable: int | None = None,
) -> dict[str, Any]:
    """Single per-round write: cost + cache with identical family/model attribution.

    Call exactly once at the end of an LLM round (not from stream adapters).
    """
    fam, mid = resolve_usage_family(
        llm_service,
        model=model,
        provider_id=provider_id,
        settings=settings,
    )
    u = finalize_usage(
        dict(usage) if isinstance(usage, dict) else {},
        family=fam,
    )
    real = bool(
        u.get("prompt_tokens") or u.get("completion_tokens") or u.get("total_tokens")
    )
    # usage_source: 1 = provider, 0 = estimate
    if real and int(u.get("usage_source") or 1) == 1:
        estimated = False
        prompt = _i(u.get("prompt_tokens"))
        completion = _i(u.get("completion_tokens"))
        cache_read = _i(u.get("cache_read_input_tokens"))
        cache_write = _i(u.get("cache_creation_input_tokens"))
        total = _i(u.get("total_tokens")) or (prompt + completion)
        billable = _i(u.get("billable_tokens"))
        if billable <= 0:
            billable = max(0, prompt - cache_read) + completion
    else:
        estimated = True
        prompt = 0
        completion = 0
        cache_read = 0
        cache_write = 0
        total = max(0, int(estimated_tokens or 0))
        billable = max(
            0,
            int(
                estimated_billable
                if estimated_billable is not None
                else estimated_tokens
                or 0
            ),
        )
        if total <= 0 and billable <= 0:
            return {
                "family": fam,
                "model": mid,
                "recorded": False,
                "reason": "empty",
            }

    report_cost_to_kernel(
        process_id=process_id,
        family=fam,
        tokens=total,
        billable=billable if billable > 0 else total,
        model=mid or None,
        prompt=prompt,
        completion=completion,
        cache_read=cache_read,
        cache_write=cache_write,
        estimated=estimated,
    )

    # Cache ledger: only for real provider usage with prompt>0
    if not estimated and prompt > 0:
        logger.info(
            "prompt cache family=%s model=%s prompt=%s cache_read=%s (%.1f%%) "
            "cache_write=%s billable=%s estimated=%s",
            fam,
            mid or "?",
            prompt,
            cache_read,
            100.0 * cache_read / prompt if prompt else 0.0,
            cache_write,
            billable,
            estimated,
        )
        _report_cache_to_kernel(
            family=fam,
            model=mid or "",
            hit=cache_read > 0,
            bytes_saved=max(0, cache_read) * 4,
            prompt_tokens=prompt,
            cache_read_tokens=cache_read,
        )

    logger.info(
        "usage charge family=%s model=%s tokens=%s billable=%s prompt=%s "
        "cache_read=%s estimated=%s",
        fam,
        mid or "?",
        total,
        billable if billable > 0 else total,
        prompt,
        cache_read,
        estimated,
    )
    return {
        "family": fam,
        "model": mid,
        "tokens": total,
        "billable": billable if billable > 0 else total,
        "prompt": prompt,
        "completion": completion,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "estimated": estimated,
        "recorded": True,
    }
