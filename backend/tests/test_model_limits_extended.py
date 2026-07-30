# -*- coding: utf-8 -*-
from backend.core.model_limits import infer_context_window, limits_for_model


def test_mimo_window():
    assert infer_context_window("mimo-v2.5") >= 256_000
    assert infer_context_window("mimo-v2.5-pro") >= 256_000


def test_minimax_window():
    assert infer_context_window("MiniMax-M2.5") >= 128_000


def test_glm45():
    assert infer_context_window("glm-4.5") == 128_000


def test_deepseek_r1():
    assert infer_context_window("deepseek-r1") == 128_000


def test_limits_dict():
    d = limits_for_model("mimo-v2.5")
    assert d["context_window"] >= 256_000
    assert d["max_tokens"] >= 4096
