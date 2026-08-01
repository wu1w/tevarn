"""Optional disk persistence for chat run snapshots (crash / multi-worker recovery).

Enabled when settings.agent_run_snapshot_persist is True (default True for durability).
Files: {TAKTON_HOME or ~/.takton}/run_snapshots/{session_id}.json

Privacy (P2):
  Disk is **local-first** — not a multi-tenant store. On shared machines / multi-user
  same HOME, anyone who can read ~/.takton can see in-flight partial text.

  By default disk writes **redact tool result bodies** and cap partial_content
  (``agent_run_snapshot_disk_full_tools=False``). In-memory snapshots stay full for
  live WS reconnect UX. Multi-worker same session may race-replace the file
  (sticky session recommended).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Disk caps (privacy + size); in-memory path is separate.
_DISK_PARTIAL_MAX = 24_000
_DISK_TOOL_RESULT_MAX = 200
_DISK_ARG_VALUE_MAX = 80
_DISK_ARG_KEYS_MAX = 12


def _enabled() -> bool:
    try:
        from backend.core.config import settings

        return bool(getattr(settings, "agent_run_snapshot_persist", True))
    except Exception:
        return True


def _full_tools_on_disk() -> bool:
    """True → keep full tool results on disk (dev/debug only)."""
    try:
        from backend.core.config import settings

        return bool(getattr(settings, "agent_run_snapshot_disk_full_tools", False))
    except Exception:
        return False


def _dir() -> Path:
    try:
        from backend.agent._takton_paths import host_home

        root = Path(os.environ.get("TAKTON_HOME") or (host_home() / ".takton"))
    except Exception:
        root = Path(os.environ.get("TAKTON_HOME") or Path.home() / ".takton")
    d = root / "run_snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(session_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(session_id))
    return _dir() / f"{safe}.json"


def _redact_for_disk(data: dict[str, Any]) -> dict[str, Any]:
    """Strip/truncate sensitive tool payloads before JSON write."""
    out = dict(data)
    pc = str(out.get("partial_content") or "")
    if len(pc) > _DISK_PARTIAL_MAX:
        out["partial_content"] = pc[:_DISK_PARTIAL_MAX] + "…"
    if _full_tools_on_disk():
        out["disk_privacy"] = "full_tools"
        return out
    tools = out.get("live_tools") or []
    redacted: list[dict[str, Any]] = []
    if isinstance(tools, list):
        for t in tools:
            if not isinstance(t, dict):
                continue
            nt: dict[str, Any] = {
                "id": t.get("id") or t.get("tool_call_id"),
                "name": t.get("name") or "tool",
                "status": t.get("status"),
            }
            args = t.get("arguments")
            if isinstance(args, dict):
                slim: dict[str, Any] = {}
                for i, (k, v) in enumerate(args.items()):
                    if i >= _DISK_ARG_KEYS_MAX:
                        break
                    if isinstance(v, (dict, list)):
                        slim[str(k)] = f"<{type(v).__name__}>"
                    else:
                        s = str(v)
                        slim[str(k)] = (
                            s[:_DISK_ARG_VALUE_MAX] + "…"
                            if len(s) > _DISK_ARG_VALUE_MAX
                            else s
                        )
                nt["arguments"] = slim
            r = t.get("result")
            if r is not None:
                try:
                    s = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
                except Exception:
                    s = str(r)
                if len(s) > _DISK_TOOL_RESULT_MAX:
                    s = s[:_DISK_TOOL_RESULT_MAX] + "…"
                nt["result"] = s
                nt["result_redacted"] = True
            redacted.append(nt)
    out["live_tools"] = redacted
    out["disk_privacy"] = "tool_results_truncated"
    return out


def save_snapshot(session_id: str, data: dict[str, Any]) -> None:
    if not _enabled() or not session_id:
        return
    try:
        payload = _redact_for_disk(
            {
                **data,
                "session_id": str(session_id),
                "persisted_at": time.time(),
            }
        )
        p = _path(session_id)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        logger.debug("run_snapshot save skip: %s", e)


def load_snapshot(session_id: str, *, max_age_sec: float = 3600.0) -> dict[str, Any] | None:
    if not _enabled() or not session_id:
        return None
    try:
        p = _path(session_id)
        if not p.is_file():
            return None
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        ts = float(raw.get("persisted_at") or raw.get("updated_at") or 0)
        if ts and (time.time() - ts) > max_age_sec:
            try:
                p.unlink(missing_ok=True)  # type: ignore[call-arg]
            except Exception:
                pass
            return None
        return raw
    except Exception as e:
        logger.debug("run_snapshot load skip: %s", e)
        return None


def delete_snapshot(session_id: str) -> None:
    if not session_id:
        return
    try:
        p = _path(session_id)
        if p.is_file():
            p.unlink()
    except Exception as e:
        logger.debug("run_snapshot delete skip: %s", e)
