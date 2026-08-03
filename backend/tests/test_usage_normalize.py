# -*- coding: utf-8 -*-
"""usage_normalize multi-provider fixtures + round recording accuracy."""
from __future__ import annotations

import json
from pathlib import Path

from backend.services.llm.usage_normalize import (
    charge_amount_from_usage,
    finalize_usage,
    map_responses_usage_to_openai,
    merge_usage,
    normalize_usage,
    record_round_usage,
    resolve_usage_family,
)


def test_anthropic_usage():
    raw = {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_input_tokens": 800,
        "cache_creation_input_tokens": 50,
    }
    u = normalize_usage(raw, family="anthropic")
    assert u["prompt_tokens"] == 100 + 800 + 50
    assert u["completion_tokens"] == 20
    assert u["cache_read_input_tokens"] == 800
    assert u["billable_tokens"] > 0


def test_openai_cached_details():
    raw = {
        "prompt_tokens": 1000,
        "completion_tokens": 50,
        "total_tokens": 1050,
        "prompt_tokens_details": {"cached_tokens": 700},
    }
    u = normalize_usage(raw, family="openai")
    assert u["cache_read_input_tokens"] == 700
    assert u["billable_input_tokens"] == 300
    assert u["billable_tokens"] == 350


def test_deepseek_hit_miss():
    raw = {
        "prompt_tokens": 1200,
        "completion_tokens": 30,
        "prompt_cache_hit_tokens": 900,
        "prompt_cache_miss_tokens": 300,
    }
    u = normalize_usage(raw, family="deepseek")
    assert u["cache_read_input_tokens"] == 900
    assert u["billable_input_tokens"] == 300
    assert charge_amount_from_usage(u, prefer_billable=True) == 330


def test_glm_details():
    raw = {
        "prompt_tokens": 500,
        "completion_tokens": 10,
        "prompt_tokens_details": {"cached_tokens": 200},
    }
    u = normalize_usage(raw, family="glm")
    assert u["cache_read_input_tokens"] == 200
    assert u["billable_input_tokens"] == 300


def test_empty_raw():
    assert normalize_usage(None) == {}
    assert normalize_usage({}) == {}
    assert charge_amount_from_usage(None, fallback=42) == 42


def test_prefer_billable_false():
    u = {
        "prompt_tokens": 1000,
        "completion_tokens": 10,
        "billable_tokens": 100,
        "billable_input_tokens": 90,
        "cache_read_input_tokens": 900,
    }
    assert charge_amount_from_usage(u, prefer_billable=False) == 1010
    assert charge_amount_from_usage(u, prefer_billable=True) == 100


def test_responses_api_not_anthropic_branch():
    """Codex / Responses: input_tokens + input_tokens_details.cached_tokens."""
    raw = {
        "input_tokens": 10000,
        "output_tokens": 4472,
        "total_tokens": 14472,
        "input_tokens_details": {"cached_tokens": 2000},
    }
    # Must NOT treat as Anthropic (would drop nested cached_tokens)
    u = normalize_usage(raw, family="openai-chatgpt-oauth")
    assert u["prompt_tokens"] == 10000
    assert u["completion_tokens"] == 4472
    assert u["cache_read_input_tokens"] == 2000
    assert u["billable_input_tokens"] == 8000
    assert u["billable_tokens"] == 8000 + 4472


def test_map_responses_usage_to_openai():
    raw = {
        "input_tokens": 500,
        "output_tokens": 20,
        "input_tokens_details": {"cached_tokens": 100},
    }
    m = map_responses_usage_to_openai(raw)
    assert m["prompt_tokens"] == 500
    assert m["completion_tokens"] == 20
    assert m["prompt_tokens_details"]["cached_tokens"] == 100
    u = normalize_usage(m, family="openai-chatgpt-oauth")
    assert u["cache_read_input_tokens"] == 100


def test_merge_and_finalize_partial_stream():
    acc: dict[str, int] = {}
    merge_usage(
        acc,
        normalize_usage(
            {
                "input_tokens": 100,
                "cache_read_input_tokens": 80,
                "output_tokens": 0,
            },
            family="anthropic",
        ),
    )
    merge_usage(
        acc,
        normalize_usage({"output_tokens": 25}, family="anthropic"),
    )
    # partial second chunk may zero prompt — merge keeps max prompt
    assert acc["prompt_tokens"] >= 180  # 100+80+0 creation
    fin = finalize_usage(acc, family="anthropic")
    assert fin["completion_tokens"] == 25
    assert fin["billable_tokens"] == fin["billable_input_tokens"] + 25


def test_record_round_usage_real_and_estimated(tmp_path: Path, monkeypatch):
    from backend.services import usage_ledger as ul

    ledger = tmp_path / "usage_ledger.json"
    monkeypatch.setenv("TAKTON_USAGE_LEDGER", str(ledger))
    ul.reset_for_tests()

    class Svc:
        provider_id = "opencode-go"
        model = "deepseek-v4-flash"

        def _family(self):
            return "opencode-go"

    r = record_round_usage(
        usage={
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "total_tokens": 1050,
            "cache_read_input_tokens": 400,
            "billable_input_tokens": 600,
            "billable_tokens": 650,
            "usage_source": 1,
        },
        llm_service=Svc(),
        process_id="p1",
    )
    assert r["recorded"] is True
    assert r["estimated"] is False
    assert r["family"] == "opencode-go"
    assert r["cache_read"] == 400

    snap = ul.snapshot_cost()
    assert snap["totals"]["prompt"] == 1000
    assert snap["totals"]["cache_read"] == 400
    assert snap["totals"]["real_rounds"] == 1
    assert snap["by_model"]["opencode-go/deepseek-v4-flash"]["cache_read"] == 400

    csnap = ul.snapshot_cache()
    assert csnap["totals"]["prompt_tokens"] == 1000
    assert csnap["totals"]["cache_read_tokens"] == 400
    assert abs(csnap["totals"]["token_hit_rate"] - 0.4) < 1e-9
    assert csnap["models"]["opencode-go/deepseek-v4-flash"]["family"] == "opencode-go"

    # estimated round: no cache write
    r2 = record_round_usage(
        usage=None,
        llm_service=Svc(),
        process_id="p2",
        estimated_tokens=900,
        estimated_billable=900,
    )
    assert r2["estimated"] is True
    snap2 = ul.snapshot_cost()
    assert snap2["totals"]["estimated_rounds"] == 1
    assert snap2["totals"]["real_rounds"] == 1
    # cache still only from real round
    csnap2 = ul.snapshot_cache()
    assert csnap2["totals"]["hits"] + csnap2["totals"]["misses"] == 1


def test_resolve_usage_family_prefers_provider_id():
    class Svc:
        provider_id = "openai-chatgpt-oauth"
        model = "gpt-5.6-luna"

        def _family(self):
            return "generic"

    fam, mid = resolve_usage_family(Svc())
    assert fam == "openai-chatgpt-oauth"
    assert mid == "gpt-5.6-luna"


def test_ledger_persists(tmp_path: Path, monkeypatch):
    from backend.services import usage_ledger as ul

    ledger = tmp_path / "usage_ledger.json"
    monkeypatch.setenv("TAKTON_USAGE_LEDGER", str(ledger))
    ul.reset_for_tests()
    ul.charge(
        process_id="x",
        family="opencode-go",
        tokens=100,
        billable=80,
        model="m1",
        prompt=90,
        completion=10,
        cache_read=20,
        estimated=False,
    )
    ul.reset_for_tests()  # force reload from disk
    snap = ul.snapshot_cost()
    assert snap["totals"]["tokens"] == 100
    assert snap["by_model"]["opencode-go/m1"]["prompt"] == 90
    assert snap["by_model"]["opencode-go/m1"]["cache_read"] == 20
    raw = json.loads(ledger.read_text(encoding="utf-8"))
    assert raw["version"] == 2
