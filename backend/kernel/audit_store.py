"""Kernel 审计事件落盘。

.. deprecated:: P0-A
    Rust host 默认写 JSONL（``AuditEventStore`` in ``takton_kernel::audit``）。
    Python 路径仅 fallback 使用；新逻辑改 crates/takton-kernel。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PATH = os.path.expanduser("~/.takton/kernel_events.jsonl")
_TAIL_LOAD_LIMIT = 200  # 启动时只回放尾部，链验证按需全量读文件


class AuditEventStore:
    """线程安全的 JSONL 追加存储 + 链尾恢复。"""

    def __init__(self, path: str | None = None) -> None:
        self._path = path or _DEFAULT_PATH
        self._lock = threading.Lock()

    @property
    def path(self) -> str:
        return self._path

    def append(self, event_dict: dict[str, Any]) -> bool:
        """追加一条事件。返回是否成功（失败告警不抛）。"""
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            line = json.dumps(event_dict, ensure_ascii=False, default=str)
            with self._lock, open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            return True
        except OSError as e:
            logger.warning("kernel 审计落盘失败（不阻断）: %s", e)
            return False

    def load_tail_hash(self) -> str | None:
        """读文件尾部最后一条事件的 hash——用于重启后续链。"""
        try:
            if not os.path.isfile(self._path):
                return None
            last_hash: str | None = None
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        h = json.loads(line).get("hash")
                    except json.JSONDecodeError:
                        continue
                    if h:
                        last_hash = h
            return last_hash
        except OSError:
            return None

    def read_after(self, after_hash: str | None, *, limit: int = 10000) -> list[dict[str, Any]]:
        """增量读取：返回 after_hash 之后的事件（按文件顺序）。

        0.5 checkpoint 恢复路径用：恢复 = 最新快照 + 快照 tail_hash 之后的
        增量事件——禁止全量 replay（PLAN §3.b 红线）。
        after_hash=None 表示从头读（仅限无快照的首次启动）。
        找不到 after_hash（文件被截断/篡改）时返回空列表并告警——
        调用方应触发链验证定位问题，而不是默默从头读。
        """
        out: list[dict[str, Any]] = []
        found_anchor = after_hash is None
        try:
            if not os.path.isfile(self._path):
                return out
            with open(self._path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not found_anchor:
                        if e.get("hash") == after_hash:
                            found_anchor = True
                        continue
                    out.append(e)
                    if len(out) >= limit:
                        break
        except OSError:
            return []
        if not found_anchor and after_hash is not None:
            logger.warning(
                "checkpoint tail_hash 在事件文件中未找到（截断/篡改？），增量恢复为空"
            )
        return out

    def verify_file_chain(self) -> tuple[bool, int]:
        """全量验证落盘文件哈希链。返回 (是否完整, 断链行号或 -1)。"""
        from backend.kernel.kernel import _event_hash

        prev = None
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        return False, lineno
                    expected = _event_hash(
                        str(e.get("prev_hash") or ""),
                        str(e.get("kind") or ""),
                        str(e.get("process_id") or ""),
                        e.get("detail") or {},
                        float(e.get("ts") or 0),
                        str(e.get("id") or ""),
                    )
                    if e.get("hash") != expected:
                        return False, lineno
                    if prev is not None and e.get("prev_hash") != prev:
                        return False, lineno
                    prev = e.get("hash")
        except OSError:
            return False, 0
        return True, -1
