

def test_threshold_reads_settings_override() -> None:
    """Alpha Review #3：阈值参数化——settings 覆盖优先，常量仅兜底。"""
    from backend.core.config import settings
    from backend.kernel.evolution_engine import _threshold

    # 默认：读 settings 默认值（与常量一致）
    assert _threshold("_MIN_SAMPLES") == 5
    assert _threshold("_DEPRECATE_DENIAL_RATE") == 0.5

    # 覆盖：运营型身份场景可调低样本阈值
    old = settings.agent_evolution_min_samples
    try:
        settings.agent_evolution_min_samples = 2
        assert _threshold("_MIN_SAMPLES") == 2
    finally:
        settings.agent_evolution_min_samples = old
    assert _threshold("_MIN_SAMPLES") == 5  # 恢复
