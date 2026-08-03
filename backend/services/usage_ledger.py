"""Durable LLM usage / cache ledger (survives kernel-host restarts).

The Rust kernel cost_panel is in-process memory only — every host restart
zeros the UI. This module dual-writes the same charges to a JSON file under
the Takton data dir so the 用量 page keeps accumulating.

Accuracy fields (for compression strategy):
  prompt / completion / cache_read / cache_write per family+model
  real_rounds vs estimated_rounds
  token-level cache hit rate = sum(cache_read) / sum(prompt)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_state: dict[str, Any] | None = None
_path: Path | None = None


def _data_dir() -> Path:
    env = (os.environ.get("TAKTON_DATA_DIR") or "").strip()
    if env:
        return Path(env)
    appdata = (os.environ.get("APPDATA") or "").strip()
    if appdata:
        return Path(appdata) / "takton" / "data"
    return Path.home() / ".takton" / "data"


def ledger_path() -> Path:
    global _path
    if _path is None:
        override = (os.environ.get("TAKTON_USAGE_LEDGER") or "").strip()
        _path = Path(override) if override else (_data_dir() / "usage_ledger.json")
    return _path


def _empty_cost_bucket() -> dict[str, Any]:
    return {
        "tokens": 0,
        "billable": 0,
        "rounds": 0,
        "prompt": 0,
        "completion": 0,
        "cache_read": 0,
        "cache_write": 0,
        "real_rounds": 0,
        "estimated_rounds": 0,
    }


def _empty_cache_bucket() -> dict[str, Any]:
    return {
        "hits": 0,
        "misses": 0,
        "bytes_saved": 0,
        "hit_rate": 0.0,
        "prompt_tokens": 0,
        "cache_read_tokens": 0,
        "token_hit_rate": 0.0,
    }


def _empty() -> dict[str, Any]:
    return {
        "version": 2,
        "totals": {
            "tokens": 0,
            "billable": 0,
            "llm_rounds": 0,
            "prompt": 0,
            "completion": 0,
            "cache_read": 0,
            "cache_write": 0,
            "real_rounds": 0,
            "estimated_rounds": 0,
        },
        "by_family": {},
        "by_model": {},
        "by_process": {},
        "cache": {
            "totals": {
                "hits": 0,
                "misses": 0,
                "bytes_saved": 0,
                "hit_rate": 0.0,
                "prompt_tokens": 0,
                "cache_read_tokens": 0,
                "token_hit_rate": 0.0,
            },
            "families": {},
            "models": {},
        },
        "updated_at": 0.0,
    }


def _model_key(family: str, model: str) -> str:
    m = (model or "").strip()
    fam = (family or "default").strip() or "default"
    return f"{fam}/(default)" if not m else f"{fam}/{m}"


def _ensure_cost_fields(bucket: dict[str, Any]) -> dict[str, Any]:
    base = _empty_cost_bucket()
    for k, v in base.items():
        if k not in bucket:
            bucket[k] = v
    return bucket


def _ensure_cache_fields(bucket: dict[str, Any]) -> dict[str, Any]:
    base = _empty_cache_bucket()
    for k, v in base.items():
        if k not in bucket:
            bucket[k] = v
    return bucket


def _recompute_cache_rates(bucket: dict[str, Any]) -> None:
    h = int(bucket.get("hits") or 0)
    m = int(bucket.get("misses") or 0)
    bucket["hit_rate"] = (h / (h + m)) if (h + m) > 0 else 0.0
    pt = int(bucket.get("prompt_tokens") or 0)
    cr = int(bucket.get("cache_read_tokens") or 0)
    if cr > pt > 0:
        cr = pt
        bucket["cache_read_tokens"] = cr
    bucket["token_hit_rate"] = (cr / pt) if pt > 0 else 0.0


def _load_unlocked() -> dict[str, Any]:
    global _state
    if _state is not None:
        return _state
    path = ledger_path()
    data = _empty()
    try:
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = _empty()
                data["totals"] = {
                    **data["totals"],
                    **(raw.get("totals") if isinstance(raw.get("totals"), dict) else {}),
                }
                for key in ("by_family", "by_model", "by_process"):
                    v = raw.get(key)
                    if isinstance(v, dict):
                        data[key] = v
                cache = raw.get("cache")
                if isinstance(cache, dict):
                    ct = cache.get("totals") if isinstance(cache.get("totals"), dict) else {}
                    data["cache"]["totals"].update(
                        {
                            k: ct.get(k, data["cache"]["totals"].get(k))
                            for k in data["cache"]["totals"]
                        }
                    )
                    if isinstance(cache.get("families"), dict):
                        data["cache"]["families"] = cache["families"]
                    if isinstance(cache.get("models"), dict):
                        data["cache"]["models"] = cache["models"]
                data["updated_at"] = float(raw.get("updated_at") or 0)
                data["version"] = int(raw.get("version") or 2)
    except Exception as e:
        logger.warning("usage_ledger load failed (%s): %s", path, e)
        data = _empty()
    _state = data
    return _state


def _save_unlocked(data: dict[str, Any]) -> None:
    path = ledger_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data["updated_at"] = time.time()
        data["version"] = 2
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(path)
    except Exception as e:
        logger.warning("usage_ledger save failed (%s): %s", path, e)


def charge(
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
) -> dict[str, Any]:
    """Accumulate one LLM round into the durable ledger."""
    fam = (family or "default").strip() or "default"
    mid = (model or "").strip()
    tok = max(0, int(tokens or 0))
    bill = max(0, int(billable or 0))
    pr = max(0, int(prompt or 0))
    co = max(0, int(completion or 0))
    cr = max(0, int(cache_read or 0))
    cw = max(0, int(cache_write or 0))
    if pr > 0 and cr > pr:
        cr = pr
    pid = (process_id or "").strip() or "system"
    if tok <= 0 and bill <= 0 and pr <= 0 and co <= 0:
        return snapshot_cost()

    with _lock:
        data = _load_unlocked()
        totals = data["totals"]
        totals["tokens"] = int(totals.get("tokens") or 0) + tok
        totals["billable"] = int(totals.get("billable") or 0) + bill
        totals["llm_rounds"] = int(totals.get("llm_rounds") or 0) + 1
        totals["prompt"] = int(totals.get("prompt") or 0) + pr
        totals["completion"] = int(totals.get("completion") or 0) + co
        totals["cache_read"] = int(totals.get("cache_read") or 0) + cr
        totals["cache_write"] = int(totals.get("cache_write") or 0) + cw
        if estimated:
            totals["estimated_rounds"] = int(totals.get("estimated_rounds") or 0) + 1
        else:
            totals["real_rounds"] = int(totals.get("real_rounds") or 0) + 1

        fc = _ensure_cost_fields(
            data["by_family"].setdefault(fam, _empty_cost_bucket())
        )
        fc["tokens"] = int(fc.get("tokens") or 0) + tok
        fc["billable"] = int(fc.get("billable") or 0) + bill
        fc["rounds"] = int(fc.get("rounds") or 0) + 1
        fc["prompt"] = int(fc.get("prompt") or 0) + pr
        fc["completion"] = int(fc.get("completion") or 0) + co
        fc["cache_read"] = int(fc.get("cache_read") or 0) + cr
        fc["cache_write"] = int(fc.get("cache_write") or 0) + cw
        if estimated:
            fc["estimated_rounds"] = int(fc.get("estimated_rounds") or 0) + 1
        else:
            fc["real_rounds"] = int(fc.get("real_rounds") or 0) + 1

        mk = _model_key(fam, mid)
        mc = _ensure_cost_fields(
            data["by_model"].setdefault(
                mk,
                {
                    **_empty_cost_bucket(),
                    "family": fam,
                    "model": mid or "(default)",
                },
            )
        )
        mc["family"] = fam
        mc["model"] = mid or "(default)"
        mc["tokens"] = int(mc.get("tokens") or 0) + tok
        mc["billable"] = int(mc.get("billable") or 0) + bill
        mc["rounds"] = int(mc.get("rounds") or 0) + 1
        mc["prompt"] = int(mc.get("prompt") or 0) + pr
        mc["completion"] = int(mc.get("completion") or 0) + co
        mc["cache_read"] = int(mc.get("cache_read") or 0) + cr
        mc["cache_write"] = int(mc.get("cache_write") or 0) + cw
        if estimated:
            mc["estimated_rounds"] = int(mc.get("estimated_rounds") or 0) + 1
        else:
            mc["real_rounds"] = int(mc.get("real_rounds") or 0) + 1

        pc = data["by_process"].setdefault(
            pid,
            {
                "tokens": 0,
                "billable": 0,
                "llm_rounds": 0,
                "prompt": 0,
                "completion": 0,
                "cache_read": 0,
            },
        )
        pc["tokens"] = int(pc.get("tokens") or 0) + tok
        pc["billable"] = int(pc.get("billable") or 0) + bill
        pc["llm_rounds"] = int(pc.get("llm_rounds") or 0) + 1
        pc["prompt"] = int(pc.get("prompt") or 0) + pr
        pc["completion"] = int(pc.get("completion") or 0) + co
        pc["cache_read"] = int(pc.get("cache_read") or 0) + cr

        _save_unlocked(data)
        logger.info(
            "usage_ledger charge family=%s model=%s tokens=%s billable=%s "
            "prompt=%s cache_read=%s estimated=%s rounds_total=%s",
            fam,
            mid or "(default)",
            tok,
            bill,
            pr,
            cr,
            estimated,
            totals["llm_rounds"],
        )
        return snapshot_cost_unlocked(data)


def cache_record(
    *,
    family: str,
    hit: bool,
    bytes_saved: int = 0,
    model: str | None = None,
    prompt_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> dict[str, Any]:
    fam = (family or "default").strip() or "default"
    mid = (model or "").strip()
    saved = max(0, int(bytes_saved or 0))
    pt = max(0, int(prompt_tokens or 0))
    crt = max(0, int(cache_read_tokens or 0))
    if pt > 0 and crt > pt:
        crt = pt

    with _lock:
        data = _load_unlocked()
        cache = data["cache"]
        totals = _ensure_cache_fields(cache["totals"])
        if hit:
            totals["hits"] = int(totals.get("hits") or 0) + 1
        else:
            totals["misses"] = int(totals.get("misses") or 0) + 1
        totals["bytes_saved"] = int(totals.get("bytes_saved") or 0) + saved
        totals["prompt_tokens"] = int(totals.get("prompt_tokens") or 0) + pt
        totals["cache_read_tokens"] = int(totals.get("cache_read_tokens") or 0) + crt
        _recompute_cache_rates(totals)

        fc = _ensure_cache_fields(
            cache["families"].setdefault(fam, _empty_cache_bucket())
        )
        if hit:
            fc["hits"] = int(fc.get("hits") or 0) + 1
        else:
            fc["misses"] = int(fc.get("misses") or 0) + 1
        fc["bytes_saved"] = int(fc.get("bytes_saved") or 0) + saved
        fc["prompt_tokens"] = int(fc.get("prompt_tokens") or 0) + pt
        fc["cache_read_tokens"] = int(fc.get("cache_read_tokens") or 0) + crt
        _recompute_cache_rates(fc)

        mk = _model_key(fam, mid)
        mc = _ensure_cache_fields(
            cache["models"].setdefault(
                mk,
                {
                    **_empty_cache_bucket(),
                    "family": fam,
                    "model": mid or "(default)",
                },
            )
        )
        mc["family"] = fam
        mc["model"] = mid or "(default)"
        if hit:
            mc["hits"] = int(mc.get("hits") or 0) + 1
        else:
            mc["misses"] = int(mc.get("misses") or 0) + 1
        mc["bytes_saved"] = int(mc.get("bytes_saved") or 0) + saved
        mc["prompt_tokens"] = int(mc.get("prompt_tokens") or 0) + pt
        mc["cache_read_tokens"] = int(mc.get("cache_read_tokens") or 0) + crt
        _recompute_cache_rates(mc)

        _save_unlocked(data)
        return snapshot_cache_unlocked(data)


def snapshot_cost_unlocked(data: dict[str, Any]) -> dict[str, Any]:
    totals = dict(data.get("totals") or {})
    # derived token-level rates for UI
    pt = int(totals.get("prompt") or 0)
    cr = int(totals.get("cache_read") or 0)
    totals["token_cache_hit_rate"] = (cr / pt) if pt > 0 else 0.0
    return {
        "totals": totals,
        "by_family": dict(data.get("by_family") or {}),
        "by_model": dict(data.get("by_model") or {}),
        "by_process": dict(data.get("by_process") or {}),
        "ts": float(data.get("updated_at") or time.time()),
        "source": "durable",
        "path": str(ledger_path()),
    }


def snapshot_cache_unlocked(data: dict[str, Any]) -> dict[str, Any]:
    cache = data.get("cache") or {}
    totals = dict(cache.get("totals") or {})
    _recompute_cache_rates(totals)
    return {
        "totals": totals,
        "families": dict(cache.get("families") or {}),
        "models": dict(cache.get("models") or {}),
        "ts": float(data.get("updated_at") or time.time()),
        "source": "durable",
        "path": str(ledger_path()),
    }


def snapshot_cost() -> dict[str, Any]:
    with _lock:
        return snapshot_cost_unlocked(_load_unlocked())


def snapshot_cache() -> dict[str, Any]:
    with _lock:
        return snapshot_cache_unlocked(_load_unlocked())


def merge_cost_panels(
    host: dict[str, Any] | None, durable: dict[str, Any] | None
) -> dict[str, Any]:
    """Prefer durable aggregates (survive restarts); keep host process detail if any."""
    d = durable if isinstance(durable, dict) else {}
    h = host if isinstance(host, dict) else {}
    d_tot = d.get("totals") if isinstance(d.get("totals"), dict) else {}
    h_tot = h.get("totals") if isinstance(h.get("totals"), dict) else {}
    use_d = int(d_tot.get("llm_rounds") or 0) > 0 or int(d_tot.get("tokens") or 0) > 0
    if use_d:
        return {
            "totals": dict(d_tot),
            "by_family": dict(d.get("by_family") or {}),
            "by_model": dict(d.get("by_model") or {}),
            "by_process": dict(d.get("by_process") or h.get("by_process") or {}),
            "ts": d.get("ts") or time.time(),
            "source": "durable",
            "host_totals": h_tot,
        }
    return {
        "totals": dict(h_tot),
        "by_family": dict(h.get("by_family") or {}),
        "by_model": dict(h.get("by_model") or {}),
        "by_process": dict(h.get("by_process") or {}),
        "ts": h.get("ts") or time.time(),
        "source": "host" if h_tot else "empty",
    }


def merge_cache_panels(
    host: dict[str, Any] | None, durable: dict[str, Any] | None
) -> dict[str, Any]:
    d = durable if isinstance(durable, dict) else {}
    h = host if isinstance(host, dict) else {}
    d_tot = d.get("totals") if isinstance(d.get("totals"), dict) else {}
    h_tot = h.get("totals") if isinstance(h.get("totals"), dict) else {}
    d_samples = (
        int(d_tot.get("hits") or 0)
        + int(d_tot.get("misses") or 0)
        + int(d_tot.get("prompt_tokens") or 0)
    )
    if d_samples > 0:
        return {
            "totals": dict(d_tot),
            "families": dict(d.get("families") or {}),
            "models": dict(d.get("models") or {}),
            "ts": d.get("ts") or time.time(),
            "source": "durable",
            "host_totals": h_tot,
        }
    return {
        "totals": dict(h_tot),
        "families": dict(h.get("families") or {}),
        "models": dict(h.get("models") or {}),
        "ts": h.get("ts") or time.time(),
        "source": "host" if h_tot else "empty",
    }


def reset_for_tests() -> None:
    """Clear in-memory state (tests only)."""
    global _state, _path
    with _lock:
        _state = None
        _path = None
