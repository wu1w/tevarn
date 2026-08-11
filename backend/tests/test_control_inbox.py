"""P0 control_inbox unit tests (no DB)."""
from backend.agent.control_inbox import (
    drop_inbox,
    format_steer_block,
    get_inbox,
)
from backend.agent.loop_decision import force_final, same_tool_failure, thrash


def test_steer_queue_fifo():
    sid = "11111111-1111-1111-1111-111111111111"
    drop_inbox(sid)
    box = get_inbox(sid)
    box.push_steer("a")
    box.push_steer("b")
    steers = box.drain_steers()
    assert [s.content for s in steers] == ["a", "b"]
    assert box.drain_steers() == []
    box.push_queue("q1")
    box.push_queue("q2")
    assert box.peek_pending_count() == 2
    assert box.pop_queued().content == "q1"
    assert box.pop_queued().content == "q2"
    assert box.pop_queued() is None


def test_format_steer_block():
    sid = "22222222-2222-2222-2222-222222222222"
    drop_inbox(sid)
    box = get_inbox(sid)
    box.push_steer("use pytest")
    block = format_steer_block(box.drain_steers())
    assert "pytest" in block
    assert "steer" in block.lower() or "Steer" in block


def test_loop_decision_notes():
    assert force_final().decision == "force_final"
    note = same_tool_failure("edit").as_controller_note()
    assert "edit" in note
    assert thrash().decision == "force_final"
