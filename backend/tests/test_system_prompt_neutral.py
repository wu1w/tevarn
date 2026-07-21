"""System prompt 模型中立性守护测试（防 Claude Code 身份污染同款坑）。

竞品教训：Claude Code 把「You are Claude」硬编码进 system prompt，
经网关转发到非 Anthropic 模型时造成身份污染。
Takton 的身份必须模型中立：固定为「You are Takton」，model 仅作为
volatile 层的一行元数据，绝不构成身份指令。
"""

from __future__ import annotations

from backend.agent.system_prompt import DEFAULT_IDENTITY, build_system_prompt

# 不得出现在身份/stable 层的厂商身份词（防止经网关转发时污染非对应模型）
_FORBIDDEN_IDENTITY_TERMS = (
    "you are claude",
    "you are chatgpt",
    "you are gpt",
    "anthropic",
    "openai",
    "as an ai developed by",
    "developed by anthropic",
    "developed by openai",
)


def _full_prompt(**kwargs) -> str:
    parts = build_system_prompt(**kwargs)
    return "\n\n".join(parts.values())


def test_default_identity_is_neutral() -> None:
    assert "Takton" in DEFAULT_IDENTITY
    low = DEFAULT_IDENTITY.lower()
    for term in _FORBIDDEN_IDENTITY_TERMS:
        assert term not in low, f"DEFAULT_IDENTITY 含厂商身份污染词: {term}"


def test_stable_layer_has_no_vendor_identity() -> None:
    """stable 层（身份所在层）必须模型中立。"""
    for model in ("claude-sonnet-4", "gpt-4o", "qwen3.5-122b", "kimi-for-coding", None):
        parts = build_system_prompt(model=model, tools_enabled=["file_read"])
        stable = parts["stable"].lower()
        for term in _FORBIDDEN_IDENTITY_TERMS:
            assert term not in stable, f"model={model} 时 stable 层含污染词: {term}"
        # 身份仍是 Takton
        assert "takton" in stable


def test_model_is_metadata_not_identity() -> None:
    """model 参数只应出现在 volatile 层作元数据，不进入 stable 身份层。"""
    parts = build_system_prompt(model="claude-sonnet-4", tools_enabled=["file_read"])
    # stable 层不含具体 model 名
    assert "claude-sonnet-4" not in parts["stable"]
    # model 出现在 volatile 层的元数据行
    assert "claude-sonnet-4" in parts["volatile"]


def test_identity_override_still_checked_by_caller() -> None:
    """identity 可被覆盖（用户自定义人格），但默认路径不被静默注入厂商身份。"""
    parts = build_system_prompt(tools_enabled=[])
    assert DEFAULT_IDENTITY in parts["stable"]
