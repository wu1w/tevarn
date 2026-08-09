"""Pending queue for identity-memory vector index retries.

DB write is authoritative; RAG index is best-effort. When embedding/Qdrant
fails after a successful SQL insert, we enqueue the entry and flush later
so retrieval eventually catches up without blocking the user path.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from backend.agent._tevarn_paths import home_dir

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_QUEUE_NAME = "identity_index_pending.jsonl"
_MAX_QUEUE = 500
_MAX_ATTEMPTS = 8


def _path() -> Path:
    d = home_dir() / "rag"
    d.mkdir(parents=True, exist_ok=True)
    return d / _QUEUE_NAME


def _read_all() -> list[dict[str, Any]]:
    p = _path()
    if not p.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and item.get("entry_id"):
                out.append(item)
    except OSError as e:
        logger.debug("identity index queue read: %s", e)
    return out


def _write_all(items: list[dict[str, Any]]) -> None:
    p = _path()
    # atomic-ish: write temp then replace
    tmp = p.with_suffix(".tmp")
    body = "\n".join(json.dumps(it, ensure_ascii=False) for it in items[-_MAX_QUEUE:])
    if body:
        body += "\n"
    try:
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(p)
    except OSError as e:
        logger.warning("identity index queue write failed: %s", e)
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass


def enqueue(
    *,
    entry_id: str,
    identity_id: str,
    kind: str,
    content: str,
    version: int = 1,
    op: str = "upsert",
) -> None:
    """Queue one index op. op=upsert|delete."""
    eid = str(entry_id or "").strip()
    if not eid:
        return
    with _lock:
        items = _read_all()
        # de-dupe by entry_id+op — keep latest content
        items = [x for x in items if not (x.get("entry_id") == eid and x.get("op", "upsert") == op)]
        items.append(
            {
                "entry_id": eid,
                "identity_id": str(identity_id or ""),
                "kind": str(kind or "experience"),
                "content": str(content or "")[:20000],
                "version": int(version or 1),
                "op": op,
                "attempts": 0,
                "enqueued_at": time.time(),
            }
        )
        _write_all(items)
        logger.info("identity index pending enqueue entry=%s op=%s", eid[:8], op)


def pending_count() -> int:
    with _lock:
        return len(_read_all())


async def flush_pending(*, limit: int = 20) -> dict[str, int]:
    """Retry pending index ops. Returns {ok, fail, left}."""
    try:
        from backend.services.rag.capability import use_vector_rag

        if not use_vector_rag():
            return {"ok": 0, "fail": 0, "left": pending_count(), "skipped": 1}
    except Exception:
        return {"ok": 0, "fail": 0, "left": pending_count(), "skipped": 1}

    with _lock:
        items = _read_all()
    if not items:
        return {"ok": 0, "fail": 0, "left": 0}

    try:
        from backend.services.rag.factory import RAGServiceFactory

        rag = RAGServiceFactory.get_service()
    except Exception as e:
        logger.debug("identity index flush: no rag: %s", e)
        return {"ok": 0, "fail": 0, "left": len(items)}

    ok = fail = 0
    remain: list[dict[str, Any]] = []
    batch = items[: max(1, limit)]
    rest = items[max(1, limit) :]

    for it in batch:
        eid = str(it.get("entry_id") or "")
        op = str(it.get("op") or "upsert")
        attempts = int(it.get("attempts") or 0) + 1
        success = False
        try:
            if op == "delete":
                success = bool(await rag.delete_identity_memory(eid))
            else:
                success = bool(
                    await rag.upsert_identity_memory(
                        entry_id=eid,
                        identity_id=str(it.get("identity_id") or ""),
                        kind=str(it.get("kind") or "experience"),
                        content=str(it.get("content") or ""),
                        version=int(it.get("version") or 1),
                    )
                )
        except Exception as e:
            logger.debug("identity index flush item fail: %s", e)
            success = False

        if success:
            ok += 1
        else:
            fail += 1
            if attempts < _MAX_ATTEMPTS:
                it = dict(it)
                it["attempts"] = attempts
                it["last_error_at"] = time.time()
                remain.append(it)
            else:
                logger.warning(
                    "identity index drop after %s attempts entry=%s",
                    attempts,
                    eid[:8],
                )

    with _lock:
        # re-read in case concurrent enqueue
        current = _read_all()
        batch_ids = {(str(x.get("entry_id")), str(x.get("op") or "upsert")) for x in batch}
        kept = [
            x
            for x in current
            if (str(x.get("entry_id")), str(x.get("op") or "upsert")) not in batch_ids
        ]
        _write_all(kept + remain + rest)

    return {"ok": ok, "fail": fail, "left": pending_count()}
