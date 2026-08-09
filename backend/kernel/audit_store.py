"""Kernel 审计事件落盘。

.. deprecated:: P0-A
    Rust host 默认写 JSONL（``AuditEventStore`` in ``tevarn_kernel::audit``）。
    Python 路径仅 fallback 使用；新逻辑改 crates/tevarn-kernel。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_PATH = os.path.expanduser("~/.tevarn/kernel_events.jsonl")
_TAIL_LOAD_LIMIT = 200  # 启动时只回放尾部，链验证按需全量读文件
# H2-C2 rotation defaults
_MAX_BYTES = int(os.environ.get("TEVARN_AUDIT_MAX_BYTES", str(32 * 1024 * 1024)) or 0) or (
    32 * 1024 * 1024
)
_KEEP_SEGMENTS = int(os.environ.get("TEVARN_AUDIT_KEEP_SEGMENTS", "7") or 7)


def _worm_enabled() -> bool:
    v = (os.environ.get("TEVARN_AUDIT_WORM") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


class AuditEventStore:
    """线程安全的 JSONL 追加存储 + 链尾恢复 + rotation + 可选 WORM/锚点。"""

    def __init__(self, path: str | None = None) -> None:
        self._path = path or _DEFAULT_PATH
        self._lock = threading.Lock()
        self._max_bytes = _MAX_BYTES
        self._keep = max(1, _KEEP_SEGMENTS)
        self._worm = _worm_enabled()
        self._anchor_path = self._path + ".anchor.json"

    @property
    def path(self) -> str:
        return self._path

    def worm(self) -> bool:
        return self._worm

    def _rotate_if_needed(self) -> None:
        """When active file exceeds max_bytes, rotate (WORM never deletes)."""
        try:
            if not os.path.isfile(self._path):
                return
            if os.path.getsize(self._path) < self._max_bytes:
                return
            base = self._path
            if self._worm:
                import time as _t

                sealed = f"{base}.worm.{int(_t.time())}"
                try:
                    os.replace(base, sealed)
                    logger.info("WORM audit sealed %s → %s", base, sealed)
                except OSError as e:
                    logger.warning("WORM seal failed: %s", e)
                return
            # cascade: .(keep-1) <- ... <- .1 <- active
            for i in range(self._keep - 1, 0, -1):
                src = f"{base}.{i}"
                dst = f"{base}.{i + 1}"
                if os.path.isfile(src):
                    try:
                        if os.path.isfile(dst):
                            os.remove(dst)
                        os.replace(src, dst)
                    except OSError:
                        pass
            oldest = f"{base}.{self._keep + 1}"
            if os.path.isfile(oldest):
                try:
                    os.remove(oldest)
                except OSError:
                    pass
            rotated = f"{base}.1"
            try:
                if os.path.isfile(rotated):
                    os.remove(rotated)
                os.replace(base, rotated)
                logger.info("H2 audit rotated %s → %s", base, rotated)
            except OSError as e:
                logger.warning("audit rotate failed: %s", e)
        except OSError as e:
            logger.debug("audit rotate check: %s", e)

    def write_anchor(self, tip_hash: str, prev_hash: str = "") -> bool:
        """External anchor tip for offline integrity (not WORM of content itself)."""
        try:
            import hashlib
            import time as _t

            body = {
                "tip_hash": tip_hash,
                "prev_hash": prev_hash or "",
                "path": self._path,
                "worm": self._worm,
                "anchored_at": _t.time(),
                "schema": "tevarn-audit-anchor-v1",
            }
            raw = json.dumps(body, sort_keys=True, ensure_ascii=False)
            body["anchor_hash"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            os.makedirs(os.path.dirname(self._anchor_path) or ".", exist_ok=True)
            with open(self._anchor_path, "w", encoding="utf-8") as f:
                json.dump(body, f, ensure_ascii=False, indent=2)
            return True
        except OSError as e:
            logger.debug("anchor write: %s", e)
            return False

    def verify_anchor(self) -> dict[str, Any]:
        tail = self.load_tail_hash()
        tip = None
        try:
            if os.path.isfile(self._anchor_path):
                with open(self._anchor_path, encoding="utf-8") as f:
                    tip = (json.load(f) or {}).get("tip_hash")
        except Exception:
            tip = None
        ok = (tail == tip) if (tail or tip) else True
        return {
            "ok": ok,
            "worm": self._worm,
            "tail_hash": tail,
            "anchor_tip": tip,
            "anchor_path": self._anchor_path,
            "audit_path": self._path,
        }

    def append(self, event_dict: dict[str, Any]) -> bool:
        """追加一条事件。返回是否成功（失败告警不抛）。"""
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            line = json.dumps(event_dict, ensure_ascii=False, default=str)
            with self._lock:
                self._rotate_if_needed()
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                h = str(event_dict.get("hash") or "")
                if h:
                    self.write_anchor(h, str(event_dict.get("prev_hash") or ""))
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
