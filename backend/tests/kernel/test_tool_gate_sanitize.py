"""tool_gate must never ship ConnectionManager into mediate RPC."""

from __future__ import annotations

import json

from backend.kernel.tool_gate import sanitize_args_for_kernel


class ConnectionManager:  # stand-in for backend.api.websocket.ConnectionManager
    def __init__(self) -> None:
        self.n = 1


def test_sanitize_strips_ws_manager() -> None:
    cm = ConnectionManager()
    raw = {
        "path": "README.md",
        "pattern": "foo",
        "_ws_manager": cm,
        "ws_manager": cm,
        "_run_recorder": object(),
        "_session_id": "sess-1",
        "_kernel_process_id": "proc-1",
        "nested": {"ok": True, "n": 2},
    }
    clean = sanitize_args_for_kernel(raw)
    assert "_ws_manager" not in clean
    assert "ws_manager" not in clean
    assert "_run_recorder" not in clean
    assert clean["path"] == "README.md"
    assert clean["pattern"] == "foo"
    assert clean["_session_id"] == "sess-1"
    # must be JSON-serializable without default=
    json.dumps(clean)


def test_sanitize_empty() -> None:
    assert sanitize_args_for_kernel(None) == {}
    assert sanitize_args_for_kernel("x") == {}  # type: ignore[arg-type]


def test_rust_decide_tool_survives_connection_manager() -> None:
    """permission_court must not fail-closed on live _ws_manager inject."""
    from backend.kernel.permission_court import _try_rust_decide_tool

    class ConnectionManager:
        pass

    d = _try_rust_decide_tool(
        "file_read",
        {
            "path": "README.md",
            "_ws_manager": ConnectionManager(),
            "_session_id": "s1",
        },
    )
    # host may be down in pure unit CI — only assert when we get a decision
    if d is not None:
        assert d.verdict in ("allow", "deny", "ask")
        assert d.matched_rule != "court:rust_unavailable"
