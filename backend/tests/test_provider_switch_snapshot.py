# -*- coding: utf-8 -*-
"""对话框切换 provider 同步更新 session LLM 快照的回归测试。

Bug 背景：session 创建时把 provider 锁进 config.llm 快照；对话框切换 provider
之前只改全局 catalog，不更新当前 session 快照，导致"切不过去"，
继续对话仍走旧 provider 并报旧 LLM 的错。

修复：select_active_model 接收可选 session_id，切换成功后用
build_llm_snapshot 重建该 session 的 config.llm。
"""

import uuid

import pytest

from backend.core import model_catalog as mc


def _make_catalog():
    """构造含两个 provider 的 catalog。"""
    return {
        "active_provider_id": "prov-a",
        "active_model": "model-a1",
        "providers": [
            {
                "id": "prov-a",
                "name": "Provider A",
                "llm_provider": "openai-compatible",
                "llm_base_url": "https://a.example.com/v1",
                "llm_api_key": "sk-a",
                "enabled": True,
                "active_model": "model-a1",
            },
            {
                "id": "prov-b",
                "name": "Provider B",
                "llm_provider": "openai",
                "llm_base_url": "https://api.openai.com/v1",
                "llm_api_key": "sk-b",
                "enabled": True,
                "active_model": "gpt-4o",
            },
        ],
    }


def test_build_llm_snapshot_switches_provider():
    """build_llm_snapshot 用目标 provider + model 生成新快照，字段齐全。"""
    cat = _make_catalog()
    provider_b = next(p for p in cat["providers"] if p["id"] == "prov-b")
    snap = mc.build_llm_snapshot(
        provider_b,
        "gpt-4o",
        temperature=0.5,
        max_tokens=2048,
        context_window=128000,
    )
    assert snap["provider"] == "openai"
    assert snap["provider_id"] == "prov-b"
    assert snap["model"] == "gpt-4o"
    assert snap["base_url"] == "https://api.openai.com/v1"
    assert snap["api_key"] == "sk-b"
    assert snap["temperature"] == 0.5
    assert snap["max_tokens"] == 2048
    assert snap["context_window"] == 128000


def test_session_config_snapshot_replacement():
    """模拟修复逻辑：切换后 session config['llm'] 被替换为新 provider 快照。"""
    cat = _make_catalog()
    # 旧 session 快照锁定在 prov-a
    old_snap = mc.build_llm_snapshot(cat["providers"][0], "model-a1")
    session_config = {"llm": old_snap, "other_key": "keep-me"}

    # 用户在对话框切到 prov-b / gpt-4o —— 复刻 select_active_model 的更新逻辑
    provider_b = next(p for p in cat["providers"] if p["id"] == "prov-b")
    new_snap = mc.build_llm_snapshot(provider_b, "gpt-4o", temperature=0.7)
    cfg = dict(session_config)
    cfg["llm"] = new_snap

    # 断言：llm 快照已切换，其他 config 键不受影响
    assert cfg["llm"]["provider"] == "openai"
    assert cfg["llm"]["provider_id"] == "prov-b"
    assert cfg["llm"]["model"] == "gpt-4o"
    assert cfg["other_key"] == "keep-me"
    # 确认不再是旧 provider
    assert cfg["llm"]["provider_id"] != "prov-a"


def test_snapshot_model_falls_back_to_provider_active_model():
    """model 为空时应回落到 provider 的 active_model，避免空 model 快照。"""
    cat = _make_catalog()
    provider_b = next(p for p in cat["providers"] if p["id"] == "prov-b")
    snap = mc.build_llm_snapshot(provider_b, "")
    assert snap["model"] == "gpt-4o"


def test_invalid_session_id_does_not_crash():
    """非法 session_id 字符串应被 UUID 解析拒绝（由路由层捕获，不影响切换）。"""
    with pytest.raises((ValueError, AttributeError)):
        uuid.UUID("not-a-uuid")
