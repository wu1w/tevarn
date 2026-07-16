"""SQLite store for evolution assets, runs, trajectories."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from backend.evolution.config import get_evolution_config

_lock = threading.RLock()
_initialized: Path | None = None


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    cfg = get_evolution_config()
    path = cfg.resolve_db_path()
    with _lock:
        global _initialized
        conn = _connect(path)
        try:
            if _initialized != path:
                _init_schema(conn)
                _initialized = path
            yield conn
            conn.commit()
        finally:
            conn.close()


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS evo_assets (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            summary TEXT,
            source TEXT NOT NULL DEFAULT 'auto',
            status TEXT NOT NULL DEFAULT 'draft',
            use_count INTEGER NOT NULL DEFAULT 0,
            view_count INTEGER NOT NULL DEFAULT 0,
            last_used_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            session_id_origin TEXT,
            content TEXT,
            gen INTEGER NOT NULL DEFAULT 0,
            last_score REAL,
            meta_json TEXT,
            UNIQUE(kind, name, gen)
        );
        CREATE INDEX IF NOT EXISTS idx_evo_assets_source ON evo_assets(source);
        CREATE INDEX IF NOT EXISTS idx_evo_assets_status ON evo_assets(status);
        CREATE INDEX IF NOT EXISTS idx_evo_assets_use ON evo_assets(use_count);

        CREATE TABLE IF NOT EXISTS evo_runs (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            task_id TEXT,
            score REAL,
            status TEXT,
            failure_codes TEXT,
            detail_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evo_trajectories (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            turn INTEGER,
            tools_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evo_tasks (
            id TEXT PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            domain TEXT,
            description TEXT,
            criteria_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            run_count INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'seed',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evo_observations (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            fingerprint TEXT NOT NULL,
            tools_json TEXT,
            user_input TEXT,
            final_content TEXT,
            failure_codes TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_evo_obs_fp ON evo_observations(fingerprint);

        CREATE TABLE IF NOT EXISTS evo_clusters (
            fingerprint TEXT PRIMARY KEY,
            hit_count INTEGER NOT NULL DEFAULT 0,
            sample_input TEXT,
            skill_created TEXT,
            updated_at TEXT NOT NULL
        );
        """
    )


def ensure_store() -> Path:
    with _db() as conn:
        return get_evolution_config().resolve_db_path()


def upsert_task(
    *,
    name: str,
    domain: str = "general",
    description: str = "",
    criteria: list[dict[str, Any]],
    source: str = "seed",
    enabled: bool = True,
) -> dict[str, Any]:
    now = _utc()
    with _db() as conn:
        row = conn.execute("SELECT id FROM evo_tasks WHERE name=?", (name,)).fetchone()
        if row:
            conn.execute(
                """UPDATE evo_tasks SET domain=?, description=?, criteria_json=?,
                   enabled=?, source=?, updated_at=? WHERE name=?""",
                (
                    domain,
                    description,
                    json.dumps(criteria, ensure_ascii=False),
                    1 if enabled else 0,
                    source,
                    now,
                    name,
                ),
            )
            tid = row["id"]
        else:
            tid = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO evo_tasks
                   (id, name, domain, description, criteria_json, enabled, run_count, source, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,0,?,?,?)""",
                (
                    tid,
                    name,
                    domain,
                    description,
                    json.dumps(criteria, ensure_ascii=False),
                    1 if enabled else 0,
                    source,
                    now,
                    now,
                ),
            )
        return get_task(name)  # type: ignore


def get_task(name: str) -> dict[str, Any] | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM evo_tasks WHERE name=?", (name,)).fetchone()
        return _task_row(row) if row else None


def list_tasks(enabled_only: bool = False) -> list[dict[str, Any]]:
    with _db() as conn:
        q = "SELECT * FROM evo_tasks"
        if enabled_only:
            q += " WHERE enabled=1"
        q += " ORDER BY name"
        return [_task_row(r) for r in conn.execute(q).fetchall()]


def bump_task_run(name: str) -> None:
    with _db() as conn:
        conn.execute(
            "UPDATE evo_tasks SET run_count=run_count+1, updated_at=? WHERE name=?",
            (_utc(), name),
        )


def _task_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "domain": row["domain"],
        "description": row["description"],
        "criteria": json.loads(row["criteria_json"] or "[]"),
        "enabled": bool(row["enabled"]),
        "run_count": row["run_count"],
        "source": row["source"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_asset(
    *,
    kind: str,
    name: str,
    summary: str = "",
    source: str = "auto",
    status: str = "draft",
    content: str = "",
    gen: int = 0,
    session_id: str | None = None,
    last_score: float | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _utc()
    aid = str(uuid.uuid4())
    with _db() as conn:
        existing = conn.execute(
            "SELECT id, gen FROM evo_assets WHERE kind=? AND name=? ORDER BY gen DESC LIMIT 1",
            (kind, name),
        ).fetchone()

        # seed / explicit upsert: refresh latest row, do NOT stack generations every boot
        if source == "seed" and existing is not None:
            conn.execute(
                """UPDATE evo_assets SET summary=?, status=?, content=?, last_score=?,
                   meta_json=?, updated_at=?, source=?,
                   session_id_origin=COALESCE(?, session_id_origin)
                   WHERE id=?""",
                (
                    summary,
                    status,
                    content,
                    last_score,
                    json.dumps(meta or {}, ensure_ascii=False),
                    now,
                    source,
                    session_id,
                    existing["id"],
                ),
            )
            return get_asset(existing["id"])  # type: ignore

        # auto skills: new generation when same name already exists
        if existing is not None and gen == 0 and source == "auto":
            gen = int(existing["gen"]) + 1

        try:
            conn.execute(
                """INSERT INTO evo_assets
                   (id, kind, name, summary, source, status, use_count, view_count,
                    last_used_at, created_at, updated_at, session_id_origin, content,
                    gen, last_score, meta_json)
                   VALUES (?,?,?,?,?,?,0,0,NULL,?,?,?,?,?,?,?)""",
                (
                    aid,
                    kind,
                    name,
                    summary,
                    source,
                    status,
                    now,
                    now,
                    session_id,
                    content,
                    gen,
                    last_score,
                    json.dumps(meta or {}, ensure_ascii=False),
                ),
            )
        except sqlite3.IntegrityError:
            conn.execute(
                """UPDATE evo_assets SET summary=?, status=?, content=?, last_score=?,
                   meta_json=?, updated_at=?, session_id_origin=COALESCE(?, session_id_origin)
                   WHERE kind=? AND name=? AND gen=?""",
                (
                    summary,
                    status,
                    content,
                    last_score,
                    json.dumps(meta or {}, ensure_ascii=False),
                    now,
                    session_id,
                    kind,
                    name,
                    gen,
                ),
            )
            row = conn.execute(
                "SELECT id FROM evo_assets WHERE kind=? AND name=? AND gen=?",
                (kind, name, gen),
            ).fetchone()
            aid = row["id"]
    return get_asset(aid)  # type: ignore


def get_asset(asset_id: str) -> dict[str, Any] | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM evo_assets WHERE id=?", (asset_id,)).fetchone()
        return _asset_row(row) if row else None


def list_assets(
    *,
    kind: str | None = None,
    status: str | None = None,
    source: str | None = None,
    unused_only: bool = False,
    sort: str = "updated_at",
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if kind:
        clauses.append("kind=?")
        params.append(kind)
    if status:
        clauses.append("status=?")
        params.append(status)
    if source:
        clauses.append("source=?")
        params.append(source)
    if unused_only:
        clauses.append("use_count=0")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sort_col = {
        "use_count": "use_count DESC",
        "last_used_at": "last_used_at DESC NULLS LAST",
        "created_at": "created_at DESC",
        "updated_at": "updated_at DESC",
        "name": "name ASC",
    }.get(sort, "updated_at DESC")
    # SQLite has no NULLS LAST in older versions — emulate
    if "NULLS LAST" in sort_col:
        sort_col = "CASE WHEN last_used_at IS NULL THEN 1 ELSE 0 END, last_used_at DESC"
    q = f"SELECT * FROM evo_assets{where} ORDER BY {sort_col} LIMIT ?"
    params.append(limit)
    with _db() as conn:
        return [_asset_row(r) for r in conn.execute(q, params).fetchall()]


def update_asset_status(asset_id: str, status: str) -> dict[str, Any] | None:
    with _db() as conn:
        conn.execute(
            "UPDATE evo_assets SET status=?, updated_at=? WHERE id=?",
            (status, _utc(), asset_id),
        )
    return get_asset(asset_id)


def delete_asset(asset_id: str) -> bool:
    with _db() as conn:
        row = conn.execute("SELECT source FROM evo_assets WHERE id=?", (asset_id,)).fetchone()
        if not row:
            return False
        if row["source"] == "seed":
            return False
        conn.execute("DELETE FROM evo_assets WHERE id=?", (asset_id,))
        return True


def bulk_delete_unused_auto() -> int:
    """Delete auto assets with use_count=0 (unused). Returns deleted row count."""
    with _db() as conn:
        cur = conn.execute(
            "DELETE FROM evo_assets WHERE source='auto' AND use_count=0"
        )
        return int(cur.rowcount or 0)


def bump_use(name: str, kind: str = "skill") -> None:
    """Increment use_count for active asset matching name (latest gen)."""
    now = _utc()
    with _db() as conn:
        row = conn.execute(
            """SELECT id FROM evo_assets WHERE kind=? AND name=? AND status='active'
               ORDER BY gen DESC LIMIT 1""",
            (kind, name),
        ).fetchone()
        if not row:
            # try any status
            row = conn.execute(
                """SELECT id FROM evo_assets WHERE kind=? AND name=?
                   ORDER BY gen DESC LIMIT 1""",
                (kind, name),
            ).fetchone()
        if row:
            conn.execute(
                "UPDATE evo_assets SET use_count=use_count+1, last_used_at=?, updated_at=? WHERE id=?",
                (now, now, row["id"]),
            )


def stats() -> dict[str, Any]:
    with _db() as conn:
        total_auto = conn.execute(
            "SELECT COUNT(*) AS c FROM evo_assets WHERE source='auto'"
        ).fetchone()["c"]
        active = conn.execute(
            "SELECT COUNT(*) AS c FROM evo_assets WHERE status='active'"
        ).fetchone()["c"]
        draft = conn.execute(
            "SELECT COUNT(*) AS c FROM evo_assets WHERE status='draft'"
        ).fetchone()["c"]
        unused = conn.execute(
            "SELECT COUNT(*) AS c FROM evo_assets WHERE use_count=0 AND source='auto'"
        ).fetchone()["c"]
        top = conn.execute(
            """SELECT name, kind, use_count FROM evo_assets
               WHERE use_count>0 ORDER BY use_count DESC LIMIT 10"""
        ).fetchall()
        return {
            "auto_count": total_auto,
            "active_count": active,
            "draft_count": draft,
            "unused_auto_count": unused,
            "top_used": [
                {"name": r["name"], "kind": r["kind"], "use_count": r["use_count"]}
                for r in top
            ],
            "enabled": get_evolution_config().enabled,
            "auto_apply_skills": get_evolution_config().auto_apply_skills,
            "mode": get_evolution_config().mode,
        }


def add_run(
    *,
    session_id: str | None,
    task_id: str | None,
    score: float,
    status: str,
    failure_codes: list[str],
    detail: dict[str, Any] | None = None,
) -> str:
    rid = str(uuid.uuid4())
    with _db() as conn:
        conn.execute(
            """INSERT INTO evo_runs (id, session_id, task_id, score, status, failure_codes, detail_json, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                rid,
                session_id,
                task_id,
                score,
                status,
                json.dumps(failure_codes, ensure_ascii=False),
                json.dumps(detail or {}, ensure_ascii=False),
                _utc(),
            ),
        )
    return rid


def append_trajectory(session_id: str, turn: int, tools: list[dict[str, Any]]) -> None:
    with _db() as conn:
        conn.execute(
            """INSERT INTO evo_trajectories (id, session_id, turn, tools_json, created_at)
               VALUES (?,?,?,?,?)""",
            (str(uuid.uuid4()), session_id, turn, json.dumps(tools, ensure_ascii=False), _utc()),
        )


def recent_runs(limit: int = 20) -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM evo_runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "session_id": r["session_id"],
                    "task_id": r["task_id"],
                    "score": r["score"],
                    "status": r["status"],
                    "failure_codes": json.loads(r["failure_codes"] or "[]"),
                    "detail": json.loads(r["detail_json"] or "{}"),
                    "created_at": r["created_at"],
                }
            )
        return out


def _asset_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "name": row["name"],
        "summary": row["summary"] or "",
        "source": row["source"],
        "status": row["status"],
        "use_count": row["use_count"],
        "view_count": row["view_count"],
        "last_used_at": row["last_used_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "session_id_origin": row["session_id_origin"],
        "content": row["content"] or "",
        "gen": row["gen"],
        "last_score": row["last_score"],
        "meta": json.loads(row["meta_json"] or "{}"),
    }


def patch_asset_meta(asset_id: str, meta: dict[str, Any]) -> dict[str, Any] | None:
    with _db() as conn:
        conn.execute(
            "UPDATE evo_assets SET meta_json=?, updated_at=? WHERE id=?",
            (json.dumps(meta or {}, ensure_ascii=False), _utc(), asset_id),
        )
    return get_asset(asset_id)


def find_similar_asset(
    *,
    kind: str,
    name: str,
    summary: str,
    threshold: float = 0.72,
) -> dict[str, Any] | None:
    """Return latest auto asset with similar name/summary for dedupe/patch."""
    from backend.evolution.improver import text_similarity

    candidates = list_assets(kind=kind, source="auto", limit=100)
    best = None
    best_s = 0.0
    for a in candidates:
        s1 = text_similarity(name, a.get("name") or "")
        s2 = text_similarity(summary, a.get("summary") or "")
        s = max(s1, s2 * 0.9)
        if s > best_s:
            best_s = s
            best = a
    if best and best_s >= threshold:
        best = dict(best)
        best["_similarity"] = best_s
        return best
    return None


def add_observation(
    *,
    session_id: str,
    fingerprint: str,
    tools: list[dict[str, Any]],
    user_input: str = "",
    final_content: str = "",
    failure_codes: list[str] | None = None,
) -> str:
    oid = str(uuid.uuid4())
    with _db() as conn:
        conn.execute(
            """INSERT INTO evo_observations
               (id, session_id, fingerprint, tools_json, user_input, final_content, failure_codes, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                oid,
                session_id,
                fingerprint,
                json.dumps(tools, ensure_ascii=False)[:20000],
                (user_input or "")[:1000],
                (final_content or "")[:2000],
                json.dumps(failure_codes or [], ensure_ascii=False),
                _utc(),
            ),
        )
    return oid


def bump_cluster(
    fingerprint: str,
    *,
    session_id: str = "",
    sample_input: str = "",
) -> dict[str, Any]:
    now = _utc()
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM evo_clusters WHERE fingerprint=?", (fingerprint,)
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE evo_clusters SET hit_count=hit_count+1, updated_at=?,
                   sample_input=COALESCE(NULLIF(?,''), sample_input) WHERE fingerprint=?""",
                (now, sample_input or "", fingerprint),
            )
        else:
            conn.execute(
                """INSERT INTO evo_clusters (fingerprint, hit_count, sample_input, skill_created, updated_at)
                   VALUES (?,?,?,?,?)""",
                (fingerprint, 1, sample_input or "", None, now),
            )
        row = conn.execute(
            "SELECT * FROM evo_clusters WHERE fingerprint=?", (fingerprint,)
        ).fetchone()
        return {
            "fingerprint": row["fingerprint"],
            "hit_count": row["hit_count"],
            "sample_input": row["sample_input"],
            "skill_created": row["skill_created"],
            "updated_at": row["updated_at"],
        }


def mark_cluster_skill(fingerprint: str, skill_name: str) -> None:
    with _db() as conn:
        conn.execute(
            "UPDATE evo_clusters SET skill_created=?, updated_at=? WHERE fingerprint=?",
            (skill_name, _utc(), fingerprint),
        )


def list_clusters(limit: int = 50) -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM evo_clusters ORDER BY hit_count DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            {
                "fingerprint": r["fingerprint"],
                "hit_count": r["hit_count"],
                "sample_input": r["sample_input"],
                "skill_created": r["skill_created"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

