from backend.agent.context_compress import is_prompt_too_long_error


def test_detect_413():
    assert is_prompt_too_long_error("Error 413 request too large")
    assert is_prompt_too_long_error("prompt_too_long")
    assert not is_prompt_too_long_error("connection reset")
