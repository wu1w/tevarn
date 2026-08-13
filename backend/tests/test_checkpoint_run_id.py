"""Segment auto-continue: run_id must not be read off a reasoning str."""

from types import SimpleNamespace

import pytest

from backend.agent.checkpoint import recorder_run_id


def test_recorder_run_id_str_used_to_raise():
    """Live: `_rc = accumulated_reasoning` then `_rc.run_id` every 6-iter segment.

    Reproduces ``'str' object has no attribute 'run_id'`` on the old expression,
    then asserts the helper returns None instead of raising.
    """
    reasoning = "plan glob then read the repo"
    with pytest.raises(AttributeError, match="run_id"):
        _ = reasoning.run_id  # old: `_rc is not None and _rc.run_id`
    assert recorder_run_id(reasoning) is None
    assert recorder_run_id("") is None
    assert recorder_run_id(None) is None


def test_recorder_run_id_from_recorder_object():
    rec = SimpleNamespace(run_id="abc-123")
    assert recorder_run_id(rec) == "abc-123"
    assert recorder_run_id(SimpleNamespace(run_id=None)) is None
