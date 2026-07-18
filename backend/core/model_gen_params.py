# -*- coding: utf-8 -*-
"""Per-model generation parameters (temperature / max_tokens / context_window).

Stored as one settings row: key=llm_model_gen_params, JSON map
  { "<provider_id>|||<model>": { temperature, max_tokens, context_window }, ... }

Switching models loads that model's slot (or seeds from limits_for_model).
Saving generation params writes into the active model's slot.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from backend.core.model_limits import limits_for_model

logger = logging.getLogger(__name__)

SETTING_KEY = "llm_model_gen_params"
SEP = "|||"

DEFAULT_TEMPERATURE = 0.7


def make_key(provider_id: str, model: str) -> str:
    return f"{(provider_id or '').strip()}{SEP}{(model or '').strip()}"


def parse_key(key: str) -> tuple[str, str]:
    if SEP in (key or ""):
        a, b = key.split(SEP, 1)
        return a.strip(), b.strip()
    return "", (key or "").strip()


def _defaults_for_model(model: str) -> dict[str, Any]:
    lim = limits_for_model(model)
    return {
        "temperature": DEFAULT_TEMPERATURE,
        "max_tokens": int(lim["max_tokens"]),
        "context_window": int(lim["context_window"]),
    }


def _coerce_slot(raw: Any, model: str) -> dict[str, Any]:
    base = _defaults_for_model(model)
    if not isinstance(raw, dict):
        return base
    out = dict(base)
    try:
        if raw.get("temperature") is not None:
            out["temperature"] = float(raw["temperature"])
    except (TypeError, ValueError):
        pass
    try:
        if raw.get("max_tokens") is not None:
            out["max_tokens"] = max(256, int(raw["max_tokens"]))
    except (TypeError, ValueError):
        pass
    try:
        if raw.get("context_window") is not None:
            out["context_window"] = max(2048, int(raw["context_window"]))
    except (TypeError, ValueError):
        pass
    # clamp max_tokens to context
    out["max_tokens"] = min(out["max_tokens"], max(256, out["context_window"] // 2))
    # temperature range
    out["temperature"] = max(0.0, min(2.0, float(out["temperature"])))
    return out


async def load_map(repo: Any) -> dict[str, dict[str, Any]]:
    row = await repo.get_by_key(SETTING_KEY)
    if not row or row.value in (None, "", "{}"):
        return {}
    try:
        raw = row.value
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(data, dict):
            return {}
        out: dict[str, dict[str, Any]] = {}
        for k, v in data.items():
            if not isinstance(k, str):
                continue
            _, model = parse_key(k)
            out[k] = _coerce_slot(v, model)
        return out
    except Exception as e:
        logger.warning("load llm_model_gen_params failed: %s", e)
        return {}


async def save_map(repo: Any, data: dict[str, dict[str, Any]]) -> None:
    await repo.upsert(SETTING_KEY, json.dumps(data, ensure_ascii=False), "llm")


async def get_params(
    repo: Any,
    provider_id: str,
    model: str,
    *,
    create_if_missing: bool = True,
) -> dict[str, Any]:
    """Return params for provider+model; seed defaults if missing."""
    pid = (provider_id or "").strip()
    mid = (model or "").strip()
    if not mid:
        return _defaults_for_model("")
    key = make_key(pid, mid)
    m = await load_map(repo)
    if key in m:
        return dict(m[key])
    # legacy: try model-only key
    if mid in m:
        return dict(m[mid])
    slot = _defaults_for_model(mid)
    if create_if_missing and pid:
        m[key] = slot
        await save_map(repo, m)
    return dict(slot)


async def upsert_params(
    repo: Any,
    provider_id: str,
    model: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    pid = (provider_id or "").strip()
    mid = (model or "").strip()
    if not mid:
        raise ValueError("model required")
    key = make_key(pid, mid) if pid else mid
    m = await load_map(repo)
    slot = _coerce_slot({**(m.get(key) or {}), **params}, mid)
    m[key] = slot
    await save_map(repo, m)
    return dict(slot)


async def apply_params_to_global_settings(repo: Any, params: dict[str, Any]) -> None:
    """Write effective params into flat settings + memory (LLM factory reads these)."""
    from backend.core.runtime_settings import apply_settings_dict

    items = {
        "temperature": params["temperature"],
        "max_tokens": params["max_tokens"],
        "context_window": params["context_window"],
    }
    for k, v in items.items():
        await repo.upsert(k, v, "llm")
    apply_settings_dict(items, reset=False)


def params_for_snapshot(params: dict[str, Any] | None) -> dict[str, Any]:
    if not params:
        return {}
    return {
        "temperature": params.get("temperature"),
        "max_tokens": params.get("max_tokens"),
        "context_window": params.get("context_window"),
    }
