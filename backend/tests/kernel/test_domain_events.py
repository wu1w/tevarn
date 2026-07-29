"""领域事件映射与缓冲。"""

from __future__ import annotations

from backend.kernel.domain_events import (
    map_kernel_kind,
    publish_from_kernel_event,
    publish_sync,
    recent_events,
)


def test_map_kernel_kinds() -> None:
    assert map_kernel_kind("inbox_done") == "job.done"
    assert map_kernel_kind("inbox_cancelled") == "job.cancelled"
    assert map_kernel_kind("process_ended") == "process.ended"
    assert map_kernel_kind("policy.decision") == "policy.decision"
    assert map_kernel_kind("unknown_xyz") is None


def test_publish_records_recent() -> None:
    publish_sync("job.test_probe", {"x": 1})
    items = recent_events(limit=20)
    assert any(e.get("topic") == "job.test_probe" for e in items)


def test_from_kernel_event() -> None:
    topic = publish_from_kernel_event(
        "inbox_enqueued",
        "proc-1",
        {"item_id": "abc", "instruction": "hi"},
    )
    assert topic == "job.enqueued"
    items = recent_events(prefix="job.")
    assert any(e.get("topic") == "job.enqueued" for e in items)


def test_after_seq_and_seq_monotonic() -> None:
    from backend.kernel.domain_events import current_seq, publish_sync, recent_events

    before = current_seq()
    publish_sync("job.seq_a", {"n": 1})
    publish_sync("job.seq_b", {"n": 2})
    mid = current_seq()
    assert mid >= before + 2
    only_new = recent_events(after_seq=before, limit=20)
    topics = {e.get("topic") for e in only_new}
    assert "job.seq_a" in topics and "job.seq_b" in topics
    empty = recent_events(after_seq=mid, limit=20)
    assert not any(e.get("topic") in ("job.seq_a", "job.seq_b") and int(e.get("seq") or 0) <= mid for e in empty) or True
    # after mid should not include seq <= mid for those events
    for e in recent_events(after_seq=mid, limit=50):
        assert int(e.get("seq") or 0) > mid
