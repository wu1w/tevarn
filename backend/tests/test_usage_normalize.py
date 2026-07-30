# -*- coding: utf-8 -*-
"""usage_normalize multi-provider fixtures."""
from __future__ import annotations

from backend.services.llm.usage_normalize import (
    charge_amount_from_usage,
    normalize_usage,
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
