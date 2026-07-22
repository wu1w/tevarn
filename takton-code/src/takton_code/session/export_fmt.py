"""Session export/import formats (md / json / jsonl)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def write_export(
    project_root: Path,
    session_id: str,
    data: dict[str, Any],
    *,
    fmt: str = "json",
) -> Path:
    out_dir = Path(project_root) / ".takton" / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    short = (session_id or "sess")[:12]
    ts = time.strftime("%Y%m%d-%H%M%S")
    fmt = (fmt or "json").lower()
    if fmt in ("md", "markdown"):
        path = out_dir / f"{short}-{ts}.md"
        path.write_text(to_markdown(data), encoding="utf-8")
        return path
    if fmt in ("jsonl", "jsonlines"):
        path = out_dir / f"{short}-{ts}.jsonl"
        path.write_text(to_jsonl(data), encoding="utf-8")
        return path
    path = out_dir / f"{short}-{ts}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def to_markdown(data: dict[str, Any]) -> str:
    sess = data.get("session") or {}
    lines = [
        f"# Session {sess.get('slug') or sess.get('id') or ''}",
        "",
        f"- id: `{sess.get('id')}`",
        f"- title: {sess.get('title') or ''}",
        f"- mode: {sess.get('mode')}",
        f"- project: {sess.get('project_root')}",
        f"- exported_at: {data.get('exported_at')}",
        "",
    ]
    todos = data.get("todos") or []
    if todos:
        lines.append("## Todos")
        for t in todos:
            lines.append(f"- [{t.get('status')}] {t.get('content')}")
        lines.append("")
    lines.append("## Messages")
    lines.append("")
    for m in data.get("messages") or []:
        role = m.get("role") or "?"
        content = m.get("content") or ""
        if isinstance(content, list):
            content = json.dumps(content, ensure_ascii=False)
        lines.append(f"### {role}")
        lines.append("")
        lines.append(str(content))
        lines.append("")
        if m.get("tool_calls"):
            lines.append("```json")
            lines.append(json.dumps(m["tool_calls"], ensure_ascii=False, indent=2)[:8000])
            lines.append("```")
            lines.append("")
    return "\n".join(lines)


def to_jsonl(data: dict[str, Any]) -> str:
    """One JSON object per line: meta + each message (training-friendly)."""
    rows: list[str] = []
    meta = {
        "type": "session_meta",
        "session": data.get("session"),
        "todos": data.get("todos"),
        "exported_at": data.get("exported_at"),
        "format": "takton-code-jsonl-v1",
    }
    rows.append(json.dumps(meta, ensure_ascii=False))
    for m in data.get("messages") or []:
        rows.append(
            json.dumps(
                {
                    "type": "message",
                    "role": m.get("role"),
                    "content": m.get("content"),
                    "tool_calls": m.get("tool_calls"),
                    "tool_call_id": m.get("tool_call_id"),
                    "name": m.get("name"),
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(rows) + "\n"


def load_export_file(path: Path) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        meta = None
        messages = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("type") == "session_meta":
                meta = obj
            elif obj.get("type") == "message":
                messages.append(obj)
        if not meta:
            raise ValueError("jsonl missing session_meta")
        return {
            "session": meta.get("session"),
            "todos": meta.get("todos") or [],
            "messages": messages,
            "parts": [],
            "exported_at": meta.get("exported_at"),
            "format": "takton-code-jsonl-v1",
        }
    return json.loads(text)
