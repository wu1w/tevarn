"""Durable LLM usage / cache ledger (survives kernel-host restarts).

The Rust kernel cost_panel is in-process memory only — every host restart
zeros the UI. This module dual-writes the same charges to a JSON file under
the Takton data dir so the 用量 page keeps accumulating.
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


def _empty() -> dict[str, Any]:
    return {
        "version": 1,
        "totals": {"tokens": 0, "billable": 0, "llm_rounds": 0},
        "by_family": {},
        "by_model": {},
        "by_process": {},
        "cache": {
            "totals": {"hits": 0, "misses": 0, "bytes_saved": 0, "hit_rate": 0.0},
            "families": {},
            "models": {},
        },
        "updated_at": 0.0,
    }


def _model_key(family: str, model: str) -> str:
    m = (model or "").strip()
    fam = (family or "default").strip() or "default"
    return f"{fam}/(default)" if not m else f"{fam}/{m}"


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
                        {k: ct.get(k, data["cache"]["totals"].get(k)) for k in data["cache"]["totals"]}
                    )
                    if isinstance(cache.get("families"), dict):
                        data["cache"]["families"] = cache["families"]
                    if isinstance(cache.get("models"), dict):
                        data["cache"]["models"] = cache["models"]
                data["updated_at"] = float(raw.get("updated_at") or 0)
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
) -> dict[str, Any]:
    """Accumulate one LLM round into the durable ledger."""
    fam = (family or "default").strip() or "default"
    mid = (model or "").strip()
    tok = max(0, int(tokens or 0))
    bill = max(0, int(billable or 0))
    pid = (process_id or "").strip() or "system"
    if tok <= 0 and bill <= 0:
        # still count a round? skip empty
        return snapshot_cost()

    with _lock:
        data = _load_unlocked()
        totals = data["totals"]
        totals["tokens"] = int(totals.get("tokens") or 0) + tok
        totals["billable"] = int(totals.get("billable") or 0) + bill
        totals["llm_rounds"] = int(totals.get("llm_rounds") or 0) + 1

        fc = data["by_family"].setdefault(
            fam, {"tokens": 0, "billable": 0, "rounds": 0}
        )
        fc["tokens"] = int(fc.get("tokens") or 0) + tok
        fc["billable"] = int(fc.get("billable") or 0) + bill
        fc["rounds"] = int(fc.get("rounds") or 0) + 1

        mk = _model_key(fam, mid)
        mc = data["by_model"].setdefault(
            mk,
            {
                "family": fam,
                "model": mid or "(default)",
                "tokens": 0,
                "billable": 0,
                "rounds": 0,
            },
        )
        mc["family"] = fam
        mc["model"] = mid or "(default)"
        mc["tokens"] = int(mc.get("tokens") or 0) + tok
        mc["billable"] = int(mc.get("billable") or 0) + bill
        mc["rounds"] = int(mc.get("rounds") or 0) + 1

        pc = data["by_process"].setdefault(
            pid, {"tokens": 0, "billable": 0, "llm_rounds": 0}
        )
        pc["tokens"] = int(pc.get("tokens") or 0) + tok
        pc["billable"] = int(pc.get("billable") or 0) + bill
        pc["llm_rounds"] = int(pc.get("llm_rounds") or 0) + 1

        _save_unlocked(data)
        logger.info(
            "usage_ledger charge family=%s model=%s tokens=%s billable=%s rounds_total=%s",
            fam,
            mid or "(default)",
            tok,
            bill,
            totals["llm_rounds"],
        )
        return snapshot_cost_unlocked(data)


def cache_record(
    *,
    family: str,
    hit: bool,
    bytes_saved: int = 0,
    model: str | None = None,
) -> dict[str, Any]:
    fam = (family or "default").strip() or "default"
    mid = (model or "").strip()
    saved = max(0, int(bytes_saved or 0))
    with _lock:
        data = _load_unlocked()
        cache = data["cache"]
        totals = cache["totals"]
        if hit:
            totals["hits"] = int(totals.get("hits") or 0) + 1
        else:
            totals["misses"] = int(totals.get("misses") or 0) + 1
        totals["bytes_saved"] = int(totals.get("bytes_saved") or 0) + saved
        h = int(totals.get("hits") or 0)
        m = int(totals.get("misses") or 0)
        totals["hit_rate"] = (h / (h + m)) if (h + m) > 0 else 0.0

        fc = cache["families"].setdefault(
            fam, {"hits": 0, "misses": 0, "bytes_saved": 0, "hit_rate": 0.0}
        )
        if hit:
            fc["hits"] = int(fc.get("hits") or 0) + 1
        else:
            fc["misses"] = int(fc.get("misses") or 0) + 1
        fc["bytes_saved"] = int(fc.get("bytes_saved") or 0) + saved
        fh, fm = int(fc["hits"]), int(fc["misses"])
        fc["hit_rate"] = (fh / (fh + fm)) if (fh + fm) > 0 else 0.0

        mk = _model_key(fam, mid)
        mc = cache["models"].setdefault(
            mk,
            {
                "family": fam,
                "model": mid or "(default)",
                "hits": 0,
                "misses": 0,
                "bytes_saved": 0,
                "hit_rate": 0.0,
            },
        )
        mc["family"] = fam
        mc["model"] = mid or "(default)"
        if hit:
            mc["hits"] = int(mc.get("hits") or 0) + 1
        else:
            mc["misses"] = int(mc.get("misses") or 0) + 1
        mc["bytes_saved"] = int(mc.get("bytes_saved") or 0) + saved
        mh, mm = int(mc["hits"]), int(mc["misses"])
        mc["hit_rate"] = (mh / (mh + mm)) if (mh + mm) > 0 else 0.0

        _save_unlocked(data)
        return snapshot_cache_unlocked(data)


def snapshot_cost_unlocked(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "totals": dict(data.get("totals") or {}),
        "by_family": dict(data.get("by_family") or {}),
        "by_model": dict(data.get("by_model") or {}),
        "by_process": dict(data.get("by_process") or {}),
        "ts": float(data.get("updated_at") or time.time()),
        "source": "durable",
        "path": str(ledger_path()),
    }


def snapshot_cache_unlocked(data: dict[str, Any]) -> dict[str, Any]:
    cache = data.get("cache") or {}
    return {
        "totals": dict(cache.get("totals") or {}),
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
    # If durable has any activity, it is authoritative (host may be partial after restart)
    use_d = int(d_tot.get("llm_rounds") or 0) > 0 or int(d_tot.get("tokens") or 0) > 0
    if use_d:
        out = {
            "totals": dict(d_tot),
            "by_family": dict(d.get("by_family") or {}),
            "by_model": dict(d.get("by_model") or {}),
            "by_process": dict(d.get("by_process") or h.get("by_process") or {}),
            "ts": d.get("ts") or time.time(),
            "source": "durable",
            "host_totals": h_tot,
        }
        return out
    # Fallback: host only (e.g. first boot before any durable charge)
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
    d_samples = int(d_tot.get("hits") or 0) + int(d_tot.get("misses") or 0)
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
