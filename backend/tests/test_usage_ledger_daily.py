# -*- coding: utf-8 -*-
"""usage_ledger daily series + model attribution integrity."""
from __future__ import annotations

import json
from pathlib import Path

import backend.services.usage_ledger as ul


def test_charge_fills_by_model_day_and_no_double_totals(tmp_path: Path, monkeypatch):
    path = tmp_path / "usage_ledger.json"
    monkeypatch.setenv("TEVARN_USAGE_LEDGER", str(path))
    ul.reset_for_tests()

    ul.charge(
        process_id="p1",
        family="openai-chatgpt-oauth",
        model="gpt-5.6-luna",
        tokens=100,
        billable=80,
        prompt=90,
        completion=10,
        cache_read=30,
        estimated=False,
    )
    ul.charge(
        process_id="p1",
        family="openai-chatgpt-oauth",
        model="gpt-5.6-luna",
        tokens=50,
        billable=40,
        prompt=40,
        completion=10,
        cache_read=10,
        estimated=False,
    )
    ul.charge(
        process_id="p2",
        family="opencode-go",
        model="deepseek-v4-flash",
        tokens=200,
        billable=200,
        prompt=0,
        completion=0,
        estimated=True,
    )

    snap = ul.snapshot_cost()
    assert snap["totals"]["tokens"] == 350
    assert snap["totals"]["billable"] == 320
    assert snap["totals"]["llm_rounds"] == 3

    mk = "openai-chatgpt-oauth/gpt-5.6-luna"
    assert snap["by_model"][mk]["tokens"] == 150
    assert snap["by_model"][mk]["cache_read"] == 40
    assert abs(snap["by_model"][mk]["token_cache_hit_rate"] - 40 / 130) < 1e-9

    mk2 = "opencode-go/deepseek-v4-flash"
    assert snap["by_model"][mk2]["tokens"] == 200
    assert snap["by_model"][mk2]["family"] == "opencode-go"

    # no cross-talk
    assert snap["by_family"]["openai-chatgpt-oauth"]["tokens"] == 150
    assert snap["by_family"]["opencode-go"]["tokens"] == 200

    # daily
    assert snap["by_day"]
    day = next(iter(snap["by_day"].keys()))
    assert snap["by_day"][day]["tokens"] == 350
    assert snap["by_model_day"][mk][day]["tokens"] == 150
    assert snap["by_model_day"][mk2][day]["tokens"] == 200

    # sum models == totals
    s = sum(int(v.get("tokens") or 0) for v in snap["by_model"].values())
    assert s == snap["totals"]["tokens"]

    # durable merge does not invent host days
    merged = ul.merge_cost_panels({"totals": {"tokens": 1}}, snap)
    assert merged["source"] == "durable"
    assert merged["by_model_day"][mk][day]["tokens"] == 150

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "by_model_day" in raw
    assert raw.get("version") == 3
    assert raw["by_model_day"][mk][day]["tokens"] == 150

    # Reload from disk must keep by_day / by_model_day (no silent drop on restart)
    ul.reset_for_tests()
    snap2 = ul.snapshot_cost()
    assert snap2["by_day"][day]["tokens"] == 350
    assert snap2["by_model_day"][mk][day]["tokens"] == 150
    assert snap2["by_model_day"][mk2][day]["tokens"] == 200
    assert snap2["by_model"][mk]["tokens"] == 150
    assert snap2["by_family"]["opencode-go"]["tokens"] == 200

    # Second charge after reload must append same day, not cross models
    ul.charge(
        process_id="p3",
        family="opencode-go",
        model="deepseek-v4-flash",
        tokens=10,
        billable=10,
        estimated=True,
    )
    snap3 = ul.snapshot_cost()
    assert snap3["by_model"][mk]["tokens"] == 150  # unchanged
    assert snap3["by_model"][mk2]["tokens"] == 210
    assert snap3["by_model_day"][mk2][day]["tokens"] == 210
    assert snap3["by_day"][day]["tokens"] == 360
    ul.reset_for_tests()


def test_process_cost_returns_provider_fields_not_system(tmp_path: Path, monkeypatch):
    path = tmp_path / "usage_ledger.json"
    monkeypatch.setenv("TEVARN_USAGE_LEDGER", str(path))
    ul.reset_for_tests()
    ul.charge(
        process_id="proc-live",
        family="openai",
        tokens=100,
        billable=80,
        prompt=90,
        completion=10,
        cache_read=20,
        estimated=False,
    )
    got = ul.process_cost("proc-live")
    assert got["prompt"] == 90
    assert got["completion"] == 10
    assert got["cache_read"] == 20
    assert ul.process_cost("missing") == {}
    assert ul.process_cost("system") == {}
    assert ul.process_cost(None) == {}
    ul.reset_for_tests()
