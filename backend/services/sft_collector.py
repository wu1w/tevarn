"""SFT / usage-log collector: user commands + agent trajectories → local MD (+ jsonl).

Default OFF. Toggle setting key: sft_usage_log_enabled
Path: <data_root>/sft_corpus/
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SETTING_KEY = "sft_usage_log_enabled"
SETTING_PATH_KEY = "sft_usage_log_path"  # optional override

_lock = threading.RLock()
_enabled_cache: tuple[float, bool] | None = None
_CACHE_TTL = 5.0

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|password|authorization|secret)\s*[:=]\s*['\"]?([^\s'\"]{8,})",
)


def corpus_root() -> Path:
    """Resolved local directory for SFT markdown/jsonl."""
    override = os.getenv("TAKTON_SFT_CORPUS_DIR", "").strip()
    if override:
        p = Path(override)
        p.mkdir(parents=True, exist_ok=True)
        return p.resolve()

    home = os.getenv("TAKTON_HOME")
    if home:
        p = Path(home) / "sft_corpus"
    else:
        # Prefer next to uploads / data
        try:
            from backend.core.config import settings

            up = getattr(settings, "uploads_dir", None)
            if up:
                p = Path(up).resolve().parent / "sft_corpus"
            else:
                p = Path.cwd() / "data" / "sft_corpus"
        except Exception:
            user = os.getenv("USERPROFILE") or os.getenv("HOME") or "."
            p = Path(user) / ".takton" / "sft_corpus"
    p.mkdir(parents=True, exist_ok=True)
    return p.resolve()


def corpus_path_display() -> str:
    return str(corpus_root())


def _env_enabled() -> bool | None:
    v = os.getenv("TAKTON_SFT_USAGE_LOG_ENABLED")
    if v is None or v == "":
        return None
    return v.strip().lower() in {"1", "true", "yes", "on"}


async def is_enabled() -> bool:
    """Runtime check: env override > DB setting > False."""
    global _enabled_cache
    env = _env_enabled()
    if env is not None:
        return env

    import time

    now = time.time()
    if _enabled_cache and now - _enabled_cache[0] < _CACHE_TTL:
        return _enabled_cache[1]

    enabled = False
    try:
        from backend.repositories.setting_repo import AsyncSettingRepository

        repo = AsyncSettingRepository()
        row = await repo.get_by_key(SETTING_KEY)
        if row is not None:
            val = getattr(row, "value", row)
            if isinstance(val, bool):
                enabled = val
            else:
                enabled = str(val).strip().lower() in {"1", "true", "yes", "on"}
    except Exception as e:
        logger.debug("sft enabled read failed: %s", e)
        enabled = False

    _enabled_cache = (now, enabled)
    return enabled


def invalidate_enabled_cache() -> None:
    global _enabled_cache
    _enabled_cache = None


def _redact(text: str) -> str:
    if not text:
        return text
    return _SECRET_RE.sub(r"\1=***", text)


def _utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def format_sample_md(
    *,
    sample_id: str,
    session_id: str,
    user_input: str,
    assistant_output: str,
    tools: list[dict[str, Any]] | None = None,
    system_hint: str = "",
    meta: dict[str, Any] | None = None,
) -> str:
    tools = tools or []
    meta = meta or {}
    lines = [
        f"## sample `{sample_id}`",
        "",
        f"- session_id: `{session_id}`",
        f"- created_at: `{_utc()}`",
        f"- tool_rounds: {len(tools)}",
    ]
    for k, v in meta.items():
        lines.append(f"- {k}: `{v}`")
    lines += ["", "### system", ""]
    lines.append(
        _redact(system_hint)
        or "You are Takton, a helpful local agent. Follow tools and produce a final user-facing answer."
    )
    lines += ["", "### user", "", _redact(user_input or "").strip() or "(empty)", ""]

    if tools:
        lines += ["### trajectory", ""]
        for i, t in enumerate(tools, 1):
            name = t.get("name") or "?"
            args = _redact(json.dumps(t.get("arguments") or {}, ensure_ascii=False)[:800])
            result = _redact(str(t.get("result") or "")[:1500])
            ok = t.get("ok")
            lines.append(f"{i}. **tool** `{name}` ok={ok}")
            lines.append(f"   - args: `{args}`")
            lines.append(f"   - result: ")
            lines.append("```")
            lines.append(result)
            lines.append("```")
            lines.append("")

    lines += ["### assistant", "", _redact(assistant_output or "").strip() or "(empty)", "", "---", ""]
    return "\n".join(lines)


def append_sample(
    *,
    session_id: str,
    user_input: str,
    assistant_output: str,
    tools: list[dict[str, Any]] | None = None,
    system_hint: str = "",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write one SFT sample to daily MD + jsonl. Returns paths."""
    root = corpus_root()
    sample_id = str(uuid.uuid4())
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md_path = root / f"sft_{day}.md"
    jsonl_path = root / f"sft_{day}.jsonl"

    md_body = format_sample_md(
        sample_id=sample_id,
        session_id=str(session_id),
        user_input=user_input,
        assistant_output=assistant_output,
        tools=tools,
        system_hint=system_hint,
        meta=meta,
    )

    # ShareGPT-ish messages for trainers that prefer JSONL
    messages: list[dict[str, str]] = []
    if system_hint:
        messages.append({"role": "system", "content": _redact(system_hint)[:8000]})
    messages.append({"role": "user", "content": _redact(user_input or "")[:20000]})
    if tools:
        traj_bits = []
        for t in tools:
            traj_bits.append(
                f"[tool {t.get('name')}] args={json.dumps(t.get('arguments') or {}, ensure_ascii=False)[:400]} "
                f"→ {str(t.get('result') or '')[:600]}"
            )
        messages.append(
            {
                "role": "user",
                "content": "Tool trajectory:\n" + _redact("\n".join(traj_bits))[:12000],
            }
        )
    messages.append({"role": "assistant", "content": _redact(assistant_output or "")[:30000]})

    record = {
        "id": sample_id,
        "session_id": str(session_id),
        "created_at": _utc(),
        "messages": messages,
        "meta": meta or {},
    }

    with _lock:
        if not md_path.exists():
            header = (
                f"# Takton SFT 语料 · {day}\n\n"
                f"> 由「收集使用日志」功能生成。仅存本机：`{root}`\n\n"
                f"可用于指令微调（SFT）。请勿上传含隐私的日志。\n\n---\n\n"
            )
            md_path.write_text(header, encoding="utf-8")
        with md_path.open("a", encoding="utf-8") as f:
            f.write(md_body)
        with jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    logger.info("SFT sample written id=%s md=%s", sample_id, md_path)
    return {
        "id": sample_id,
        "md_path": str(md_path),
        "jsonl_path": str(jsonl_path),
        "dir": str(root),
    }


async def collect_if_enabled(
    *,
    session_id: str,
    user_input: str,
    assistant_output: str,
    tools: list[dict[str, Any]] | None = None,
    system_hint: str = "",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not await is_enabled():
        return None
    try:
        return append_sample(
            session_id=session_id,
            user_input=user_input,
            assistant_output=assistant_output,
            tools=tools,
            system_hint=system_hint,
            meta=meta,
        )
    except Exception as e:
        logger.warning("SFT collect failed: %s", e)
        return None
