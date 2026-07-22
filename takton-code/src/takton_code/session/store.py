"""Session persistence — OpenCode-inspired schema."""

from __future__ import annotations

import json
import random
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

_ADJECTIVES = [
    "shiny", "kind", "swift", "calm", "brave", "quiet", "bright", "noble",
    "clear", "keen", "bold", "gentle", "rapid", "steady", "lucid", "crisp",
]
_NOUNS = [
    "rocket", "star", "eagle", "river", "forest", "comet", "harbor", "ember",
    "orchid", "falcon", "willow", "cinder", "nexus", "prism", "aurora", "summit",
]


def poetic_slug() -> str:
    return f"{random.choice(_ADJECTIVES)}-{random.choice(_NOUNS)}"


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  slug TEXT,
  title TEXT,
  project_root TEXT,
  mode TEXT,
  agent TEXT,
  created_at REAL,
  updated_at REAL,
  status TEXT,
  llm_snapshot TEXT,
  plan_json TEXT,
  meta_json TEXT,
  parent_id TEXT,
  tokens_input INTEGER DEFAULT 0,
  tokens_output INTEGER DEFAULT 0,
  compress_count INTEGER DEFAULT 0,
  summary_additions INTEGER DEFAULT 0,
  summary_deletions INTEGER DEFAULT 0,
  summary_files INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT,
  tool_calls TEXT,
  tool_call_id TEXT,
  name TEXT,
  created_at REAL,
  FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS parts (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  turn_id TEXT,
  seq INTEGER,
  type TEXT NOT NULL,
  data TEXT NOT NULL,
  created_at REAL
);

CREATE TABLE IF NOT EXISTS settings_kv (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at REAL
);

CREATE TABLE IF NOT EXISTS checkpoints (
  session_id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  updated_at REAL
);

CREATE TABLE IF NOT EXISTS file_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  turn_id TEXT NOT NULL,
  path TEXT NOT NULL,
  content TEXT,
  created_at REAL
);

CREATE TABLE IF NOT EXISTS prompt_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  content TEXT NOT NULL,
  status TEXT DEFAULT 'queued',
  created_at REAL
);

CREATE TABLE IF NOT EXISTS todos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  content TEXT NOT NULL,
  status TEXT DEFAULT 'pending',
  position INTEGER DEFAULT 0,
  created_at REAL,
  updated_at REAL
);

CREATE TABLE IF NOT EXISTS history_points (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  label TEXT,
  turn_id TEXT,
  message_id TEXT,
  message_row_id INTEGER,
  kind TEXT,
  created_at REAL,
  parent_id TEXT,
  meta_json TEXT
);

CREATE TABLE IF NOT EXISTS history_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  point_id TEXT NOT NULL,
  path TEXT NOT NULL,
  content TEXT,
  backup_meta TEXT,
  content_hash TEXT,
  version INTEGER DEFAULT 1,
  UNIQUE(point_id, path)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_parts_session ON parts(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_snap_session ON file_snapshots(session_id, turn_id);
CREATE INDEX IF NOT EXISTS idx_queue_session ON prompt_queue(session_id, status);
CREATE INDEX IF NOT EXISTS idx_hist_session ON history_points(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_hist_files ON history_files(point_id);
"""


class SessionStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._migrate()
        await self._db.commit()

    async def _migrate(self) -> None:
        """Add columns if upgrading from older schema."""
        cur = await self.db.execute("PRAGMA table_info(sessions)")
        cols = {r[1] for r in await cur.fetchall()}
        alters = {
            "slug": "TEXT",
            "agent": "TEXT",
            "parent_id": "TEXT",
            "tokens_input": "INTEGER DEFAULT 0",
            "tokens_output": "INTEGER DEFAULT 0",
            "compress_count": "INTEGER DEFAULT 0",
            "summary_additions": "INTEGER DEFAULT 0",
            "summary_deletions": "INTEGER DEFAULT 0",
            "summary_files": "INTEGER DEFAULT 0",
            "worktree_name": "TEXT",
            "worktree_path": "TEXT",
        }
        for name, typ in alters.items():
            if name not in cols:
                await self.db.execute(f"ALTER TABLE sessions ADD COLUMN {name} {typ}")

        # history_points upgrade
        cur = await self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='history_points'"
        )
        if await cur.fetchone():
            cur = await self.db.execute("PRAGMA table_info(history_points)")
            hp_cols = {r[1] for r in await cur.fetchall()}
            for name, typ in {
                "message_id": "TEXT",
                "message_row_id": "INTEGER",
                "parent_id": "TEXT",
                "meta_json": "TEXT",
            }.items():
                if name not in hp_cols:
                    await self.db.execute(f"ALTER TABLE history_points ADD COLUMN {name} {typ}")
        cur = await self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='history_files'"
        )
        if await cur.fetchone():
            cur = await self.db.execute("PRAGMA table_info(history_files)")
            hf_cols = {r[1] for r in await cur.fetchall()}
            for name, typ in {
                "backup_meta": "TEXT",
                "content_hash": "TEXT",
                "version": "INTEGER DEFAULT 1",
            }.items():
                if name not in hf_cols:
                    await self.db.execute(f"ALTER TABLE history_files ADD COLUMN {name} {typ}")

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if not self._db:
            raise RuntimeError("store not open")
        return self._db

    async def create_session(
        self,
        *,
        project_root: str,
        mode: str = "build",
        title: str | None = None,
        llm_snapshot: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        parent_id: str | None = None,
        agent: str | None = None,
    ) -> str:
        sid = uuid.uuid4().hex
        now = time.time()
        slug = poetic_slug()
        await self.db.execute(
            "INSERT INTO sessions(id,slug,title,project_root,mode,agent,created_at,updated_at,status,"
            "llm_snapshot,plan_json,meta_json,parent_id)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                sid,
                slug,
                title or "untitled",
                project_root,
                mode,
                agent or mode,
                now,
                now,
                "active",
                json.dumps(llm_snapshot or {}, ensure_ascii=False),
                None,
                json.dumps(meta or {}, ensure_ascii=False),
                parent_id,
            ),
        )
        await self.db.commit()
        return sid

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        cur = await self.db.execute("SELECT * FROM sessions WHERE id=?", (session_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_session_by_slug(self, slug: str) -> dict[str, Any] | None:
        cur = await self.db.execute(
            "SELECT * FROM sessions WHERE slug=? ORDER BY updated_at DESC LIMIT 1", (slug,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT id,slug,title,project_root,mode,agent,created_at,updated_at,status,"
            "compress_count,tokens_input,tokens_output "
            "FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def update_session(self, session_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields = dict(fields)
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values()) + [session_id]
        await self.db.execute(f"UPDATE sessions SET {cols} WHERE id=?", vals)
        await self.db.commit()

    async def append_message(
        self,
        session_id: str,
        role: str,
        content: str | None = None,
        *,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str | None = None,
        name: str | None = None,
    ) -> int:
        cur = await self.db.execute(
            "INSERT INTO messages(session_id,role,content,tool_calls,tool_call_id,name,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                session_id,
                role,
                content,
                json.dumps(tool_calls, ensure_ascii=False) if tool_calls is not None else None,
                tool_call_id,
                name,
                time.time(),
            ),
        )
        await self.db.execute(
            "UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), session_id)
        )
        await self.db.commit()
        return int(cur.lastrowid)

    async def load_messages(self, session_id: str, *, with_ids: bool = False) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT id,role,content,tool_calls,tool_call_id,name FROM messages "
            "WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        )
        out: list[dict[str, Any]] = []
        for r in await cur.fetchall():
            msg: dict[str, Any] = {"role": r["role"], "content": r["content"]}
            if with_ids:
                msg["_row_id"] = int(r["id"])
            if r["tool_calls"]:
                try:
                    tcs = json.loads(r["tool_calls"])
                except json.JSONDecodeError:
                    tcs = None
                if tcs:
                    msg["tool_calls"] = tcs
                    if not msg.get("content"):
                        msg["content"] = None
            if r["tool_call_id"]:
                msg["tool_call_id"] = r["tool_call_id"]
            if r["name"]:
                msg["name"] = r["name"]
            out.append(msg)
        return out

    async def count_messages_until(self, session_id: str, row_id: int) -> int:
        cur = await self.db.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id=? AND id<=?",
            (session_id, row_id),
        )
        row = await cur.fetchone()
        return int(row[0] if row else 0)

    async def get_message_row(self, row_id: int) -> dict[str, Any] | None:
        cur = await self.db.execute(
            "SELECT id,session_id,role,content FROM messages WHERE id=?",
            (row_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def replace_messages(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        await self.db.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        for m in messages:
            await self.db.execute(
                "INSERT INTO messages(session_id,role,content,tool_calls,tool_call_id,name,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    session_id,
                    m.get("role"),
                    m.get("content"),
                    json.dumps(m["tool_calls"], ensure_ascii=False)
                    if m.get("tool_calls") is not None
                    else None,
                    m.get("tool_call_id"),
                    m.get("name"),
                    time.time(),
                ),
            )
        await self.db.execute(
            "UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), session_id)
        )
        await self.db.commit()

    # --- parts ---
    async def append_part(
        self,
        session_id: str,
        part: dict[str, Any],
        *,
        turn_id: str | None = None,
        seq: int | None = None,
    ) -> str:
        pid = part.get("id") or f"prt_{uuid.uuid4().hex[:16]}"
        part = {**part, "id": pid}
        if seq is None:
            cur = await self.db.execute(
                "SELECT COALESCE(MAX(seq),0)+1 FROM parts WHERE session_id=?", (session_id,)
            )
            seq = int((await cur.fetchone())[0])
        await self.db.execute(
            "INSERT OR REPLACE INTO parts(id,session_id,turn_id,seq,type,data,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                pid,
                session_id,
                turn_id,
                seq,
                part.get("type") or "text",
                json.dumps(part, ensure_ascii=False),
                time.time(),
            ),
        )
        await self.db.commit()
        return pid

    async def load_parts(self, session_id: str, limit: int = 500) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT data FROM parts WHERE session_id=? ORDER BY seq ASC LIMIT ?",
            (session_id, limit),
        )
        out = []
        for r in await cur.fetchall():
            try:
                out.append(json.loads(r[0]))
            except json.JSONDecodeError:
                pass
        return out

    # --- file snapshots (double-esc undo) ---
    async def save_file_snapshot(
        self, session_id: str, turn_id: str, path: str, content: str | None
    ) -> None:
        await self.db.execute(
            "INSERT INTO file_snapshots(session_id,turn_id,path,content,created_at) VALUES(?,?,?,?,?)",
            (session_id, turn_id, path, content, time.time()),
        )
        await self.db.commit()

    async def latest_turn_id(self, session_id: str) -> str | None:
        cur = await self.db.execute(
            "SELECT turn_id FROM file_snapshots WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (session_id,),
        )
        row = await cur.fetchone()
        return row[0] if row else None

    async def load_turn_snapshots(self, session_id: str, turn_id: str) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT path, content FROM file_snapshots WHERE session_id=? AND turn_id=? ORDER BY id",
            (session_id, turn_id),
        )
        return [{"path": r[0], "content": r[1]} for r in await cur.fetchall()]

    # --- history points (Claude-style multi-point rewind) ---
    async def create_history_point(
        self,
        *,
        point_id: str,
        session_id: str,
        label: str,
        turn_id: str | None,
        kind: str,
        created_at: float,
        files: dict[str, str | None],
    ) -> None:
        """Legacy helper — wraps v2 with inline-only content."""
        file_rows = [
            {
                "path": p,
                "content": c,
                "backup_meta": {},
                "content_hash": None,
                "version": 1,
            }
            for p, c in files.items()
        ]
        await self.create_history_point_v2(
            point_id=point_id,
            session_id=session_id,
            label=label,
            turn_id=turn_id,
            message_id=None,
            kind=kind,
            created_at=created_at,
            parent_id=None,
            meta={},
            files=file_rows,
        )

    async def create_history_point_v2(
        self,
        *,
        point_id: str,
        session_id: str,
        label: str,
        turn_id: str | None,
        message_id: str | None,
        kind: str,
        created_at: float,
        parent_id: str | None,
        meta: dict[str, Any],
        files: list[dict[str, Any]],
        message_row_id: int | None = None,
    ) -> None:
        await self.db.execute(
            "INSERT INTO history_points(id,session_id,label,turn_id,message_id,message_row_id,kind,created_at,parent_id,meta_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                point_id,
                session_id,
                label,
                turn_id,
                message_id,
                message_row_id,
                kind,
                created_at,
                parent_id,
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )
        for row in files:
            await self.db.execute(
                "INSERT OR REPLACE INTO history_files(point_id,path,content,backup_meta,content_hash,version) "
                "VALUES(?,?,?,?,?,?)",
                (
                    point_id,
                    row["path"],
                    row.get("content"),
                    json.dumps(row.get("backup_meta") or {}, ensure_ascii=False),
                    row.get("content_hash"),
                    int(row.get("version") or 1),
                ),
            )
        await self.db.commit()

    async def get_or_create_turn_history_point(
        self,
        *,
        session_id: str,
        turn_id: str,
        label: str,
        kind: str = "edit",
        message_id: str | None = None,
    ) -> dict[str, Any]:
        cur = await self.db.execute(
            "SELECT id,session_id,label,turn_id,message_id,kind,created_at,parent_id,meta_json "
            "FROM history_points "
            "WHERE session_id=? AND turn_id=? AND kind=? ORDER BY created_at DESC LIMIT 1",
            (session_id, turn_id, kind),
        )
        row = await cur.fetchone()
        if row:
            return dict(row)
        import uuid

        point_id = f"chk_{uuid.uuid4().hex[:12]}"
        now = time.time()
        await self.db.execute(
            "INSERT INTO history_points(id,session_id,label,turn_id,message_id,kind,created_at,parent_id,meta_json) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (point_id, session_id, label, turn_id, message_id, kind, now, None, "{}"),
        )
        await self.db.commit()
        return {
            "id": point_id,
            "session_id": session_id,
            "label": label,
            "turn_id": turn_id,
            "message_id": message_id,
            "kind": kind,
            "created_at": now,
        }

    async def add_history_file(self, point_id: str, path: str, content: str | None) -> None:
        cur = await self.db.execute(
            "SELECT 1 FROM history_files WHERE point_id=? AND path=?",
            (point_id, path),
        )
        if await cur.fetchone():
            return
        await self.db.execute(
            "INSERT INTO history_files(point_id,path,content,backup_meta,content_hash,version) "
            "VALUES(?,?,?,?,?,?)",
            (point_id, path, content, "{}", None, 1),
        )
        await self.db.commit()

    async def add_history_file_v2(
        self,
        point_id: str,
        *,
        path: str,
        content: str | None,
        backup_meta: dict[str, Any],
        content_hash: str | None,
        version: int,
    ) -> None:
        cur = await self.db.execute(
            "SELECT 1 FROM history_files WHERE point_id=? AND path=?",
            (point_id, path),
        )
        if await cur.fetchone():
            return
        await self.db.execute(
            "INSERT INTO history_files(point_id,path,content,backup_meta,content_hash,version) "
            "VALUES(?,?,?,?,?,?)",
            (
                point_id,
                path,
                content,
                json.dumps(backup_meta or {}, ensure_ascii=False),
                content_hash,
                int(version),
            ),
        )
        await self.db.commit()

    async def get_history_file(self, point_id: str, path: str) -> dict[str, Any] | None:
        cur = await self.db.execute(
            "SELECT path, content, backup_meta, content_hash, version FROM history_files "
            "WHERE point_id=? AND path=?",
            (point_id, path),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def next_history_file_version(self, session_id: str, path: str) -> int:
        cur = await self.db.execute(
            "SELECT MAX(f.version) FROM history_files f "
            "JOIN history_points p ON p.id=f.point_id "
            "WHERE p.session_id=? AND f.path=?",
            (session_id, path),
        )
        row = await cur.fetchone()
        mx = row[0] if row and row[0] is not None else 0
        return int(mx) + 1

    async def list_history_points(self, session_id: str, limit: int = 50) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT p.id,p.session_id,p.label,p.turn_id,p.message_id,p.message_row_id,p.kind,p.created_at,"
            "p.parent_id,p.meta_json AS meta, "
            "(SELECT COUNT(*) FROM history_files f WHERE f.point_id=p.id) AS file_count "
            "FROM history_points p WHERE p.session_id=? "
            "ORDER BY p.created_at DESC LIMIT ?",
            (session_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_history_point(self, point_id: str) -> dict[str, Any] | None:
        cur = await self.db.execute(
            "SELECT id,session_id,label,turn_id,message_id,message_row_id,kind,created_at,parent_id,meta_json "
            "FROM history_points WHERE id=?",
            (point_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def load_history_files(self, point_id: str) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT path, content FROM history_files WHERE point_id=? ORDER BY id",
            (point_id,),
        )
        return [{"path": r[0], "content": r[1]} for r in await cur.fetchall()]

    async def load_history_files_v2(self, point_id: str) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT path, content, backup_meta, content_hash, version "
            "FROM history_files WHERE point_id=? ORDER BY id",
            (point_id,),
        )
        out = []
        for r in await cur.fetchall():
            d = dict(r)
            meta = d.get("backup_meta")
            if isinstance(meta, str):
                try:
                    d["backup_meta"] = json.loads(meta)
                except json.JSONDecodeError:
                    d["backup_meta"] = {}
            out.append(d)
        return out

    async def truncate_messages_after(
        self, session_id: str, *, keep_until_id: int | None = None, keep_count: int | None = None
    ) -> int:
        """Delete messages after a point. keep_count keeps first N messages (incl system)."""
        if keep_count is not None:
            cur = await self.db.execute(
                "SELECT id FROM messages WHERE session_id=? ORDER BY id ASC",
                (session_id,),
            )
            ids = [r[0] for r in await cur.fetchall()]
            if len(ids) <= keep_count:
                return 0
            drop = ids[keep_count:]
            if not drop:
                return 0
            q = ",".join("?" * len(drop))
            await self.db.execute(
                f"DELETE FROM messages WHERE session_id=? AND id IN ({q})",
                (session_id, *drop),
            )
            await self.db.commit()
            return len(drop)
        if keep_until_id is not None:
            cur = await self.db.execute(
                "DELETE FROM messages WHERE session_id=? AND id>?",
                (session_id, keep_until_id),
            )
            await self.db.commit()
            return cur.rowcount
        return 0

    # --- prompt queue ---
    async def enqueue_prompt(self, session_id: str, content: str) -> int:
        cur = await self.db.execute(
            "INSERT INTO prompt_queue(session_id,content,status,created_at) VALUES(?,?,?,?)",
            (session_id, content, "queued", time.time()),
        )
        await self.db.commit()
        return int(cur.lastrowid)

    async def list_queue(self, session_id: str) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT id,content,status,created_at FROM prompt_queue "
            "WHERE session_id=? AND status='queued' ORDER BY id ASC",
            (session_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def dequeue_prompt(self, session_id: str) -> dict[str, Any] | None:
        items = await self.list_queue(session_id)
        if not items:
            return None
        item = items[0]
        await self.db.execute(
            "UPDATE prompt_queue SET status='done' WHERE id=?", (item["id"],)
        )
        await self.db.commit()
        return item

    async def clear_queue(self, session_id: str) -> int:
        cur = await self.db.execute(
            "UPDATE prompt_queue SET status='cancelled' WHERE session_id=? AND status='queued'",
            (session_id,),
        )
        await self.db.commit()
        return cur.rowcount

    # --- todos ---
    async def set_todos(self, session_id: str, items: list[dict[str, Any]]) -> None:
        await self.db.execute("DELETE FROM todos WHERE session_id=?", (session_id,))
        now = time.time()
        for i, it in enumerate(items):
            await self.db.execute(
                "INSERT INTO todos(session_id,content,status,position,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    session_id,
                    it.get("content") or it.get("title") or "",
                    it.get("status") or "pending",
                    i,
                    now,
                    now,
                ),
            )
        await self.db.commit()

    async def list_todos(self, session_id: str) -> list[dict[str, Any]]:
        cur = await self.db.execute(
            "SELECT id,content,status,position FROM todos WHERE session_id=? ORDER BY position",
            (session_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    # --- checkpoint / settings ---
    async def save_checkpoint(self, session_id: str, payload: dict[str, Any]) -> None:
        await self.db.execute(
            "INSERT INTO checkpoints(session_id,payload,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
            (session_id, json.dumps(payload, ensure_ascii=False), time.time()),
        )
        await self.db.commit()

    async def load_checkpoint(self, session_id: str) -> dict[str, Any] | None:
        cur = await self.db.execute(
            "SELECT payload FROM checkpoints WHERE session_id=?", (session_id,)
        )
        row = await cur.fetchone()
        if not row:
            return None
        try:
            return json.loads(row["payload"])
        except json.JSONDecodeError:
            return None

    async def clear_checkpoint(self, session_id: str) -> None:
        await self.db.execute("DELETE FROM checkpoints WHERE session_id=?", (session_id,))
        await self.db.commit()

    async def set_setting(self, key: str, value: Any) -> None:
        await self.db.execute(
            "INSERT INTO settings_kv(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), time.time()),
        )
        await self.db.commit()

    async def get_setting(self, key: str, default: Any = None) -> Any:
        cur = await self.db.execute("SELECT value FROM settings_kv WHERE key=?", (key,))
        row = await cur.fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return row["value"]

    async def all_settings(self) -> dict[str, Any]:
        cur = await self.db.execute("SELECT key,value FROM settings_kv")
        out: dict[str, Any] = {}
        for r in await cur.fetchall():
            try:
                out[r["key"]] = json.loads(r["value"])
            except json.JSONDecodeError:
                out[r["key"]] = r["value"]
        return out

    async def export_session(self, session_id: str) -> dict[str, Any]:
        sess = await self.get_session(session_id)
        msgs = await self.load_messages(session_id)
        parts = await self.load_parts(session_id)
        todos = await self.list_todos(session_id)
        return {
            "session": sess,
            "messages": msgs,
            "parts": parts,
            "todos": todos,
            "exported_at": time.time(),
            "format": "takton-code-session-v1",
        }

    async def fork_session(self, session_id: str) -> str:
        src = await self.get_session(session_id)
        if not src:
            raise ValueError("session not found")
        snap = {}
        try:
            snap = json.loads(src.get("llm_snapshot") or "{}")
        except json.JSONDecodeError:
            pass
        new_id = await self.create_session(
            project_root=src["project_root"],
            mode=src.get("mode") or "build",
            title=f"{src.get('title') or 'session'} (fork)",
            llm_snapshot=snap,
            parent_id=session_id,
            agent=src.get("agent"),
        )
        msgs = await self.load_messages(session_id)
        if msgs:
            await self.replace_messages(new_id, msgs)
        return new_id

    async def import_session_data(self, data: dict[str, Any], *, project_root: str | None = None) -> str:
        """Import export v1 / jsonl-derived dict into a new session."""
        sess = data.get("session") or {}
        root = project_root or sess.get("project_root") or str(Path.cwd())
        snap = {}
        try:
            raw = sess.get("llm_snapshot")
            if isinstance(raw, str):
                snap = json.loads(raw or "{}")
            elif isinstance(raw, dict):
                snap = raw
        except json.JSONDecodeError:
            pass
        new_id = await self.create_session(
            project_root=root,
            mode=sess.get("mode") or "build",
            title=(sess.get("title") or "imported") + " (import)",
            llm_snapshot=snap,
            agent=sess.get("agent"),
        )
        msgs = data.get("messages") or []
        cleaned = []
        for m in msgs:
            cleaned.append(
                {
                    "role": m.get("role") or "user",
                    "content": m.get("content"),
                    "tool_calls": m.get("tool_calls"),
                    "tool_call_id": m.get("tool_call_id"),
                    "name": m.get("name"),
                }
            )
        if cleaned:
            await self.replace_messages(new_id, cleaned)
        todos = data.get("todos") or []
        if todos:
            await self.set_todos(
                new_id,
                [{"content": t.get("content"), "status": t.get("status")} for t in todos],
            )
        return new_id

    async def bind_worktree(
        self,
        session_id: str,
        *,
        worktree_name: str | None,
        worktree_path: str | None,
    ) -> None:
        """Bind a git worktree path to session row (not only .takton/worktrees.json)."""
        await self.update_session(
            session_id,
            worktree_name=worktree_name or "",
            worktree_path=worktree_path or "",
        )

    async def get_worktree(self, session_id: str) -> dict[str, str | None]:
        row = await self.get_session(session_id)
        if not row:
            return {"worktree_name": None, "worktree_path": None}
        return {
            "worktree_name": row.get("worktree_name") or None,
            "worktree_path": row.get("worktree_path") or None,
        }

    async def stats_summary(self, *, days: int | None = None) -> dict[str, Any]:
        cur = await self.db.execute(
            "SELECT id,slug,title,project_root,mode,created_at,updated_at,"
            "tokens_input,tokens_output,compress_count FROM sessions ORDER BY updated_at DESC"
        )
        rows = [dict(r) for r in await cur.fetchall()]
        cutoff = None
        if days and days > 0:
            cutoff = time.time() - days * 86400
            rows = [r for r in rows if (r.get("updated_at") or 0) >= cutoff]
        tin = sum(int(r.get("tokens_input") or 0) for r in rows)
        tout = sum(int(r.get("tokens_output") or 0) for r in rows)
        cmpn = sum(int(r.get("compress_count") or 0) for r in rows)
        return {
            "sessions": len(rows),
            "tokens_input": tin,
            "tokens_output": tout,
            "tokens_total": tin + tout,
            "compress_count_sum": cmpn,
            "days": days,
            "recent": [
                {
                    "id": r.get("id"),
                    "slug": r.get("slug"),
                    "title": r.get("title"),
                    "mode": r.get("mode"),
                    "updated_at": r.get("updated_at"),
                    "tokens_input": r.get("tokens_input"),
                    "tokens_output": r.get("tokens_output"),
                }
                for r in rows[:20]
            ],
        }