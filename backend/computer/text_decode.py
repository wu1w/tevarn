"""Decode subprocess stdout/stderr on Windows without mojibake.

cmd.exe / many Windows tools emit system ANSI (e.g. GBK/cp936 on zh-CN).
Forcing UTF-8 decode produces replacement diamonds / garbage in tool panels.
"""

from __future__ import annotations

import locale
import sys
from functools import lru_cache


@lru_cache(maxsize=1)
def _windows_code_pages() -> tuple[str, ...]:
    if sys.platform != "win32":
        return ()
    out: list[str] = []
    try:
        import ctypes

        acp = int(ctypes.windll.kernel32.GetACP())  # type: ignore[attr-defined]
        oem = int(ctypes.windll.kernel32.GetOEMCP())  # type: ignore[attr-defined]
        if acp > 0:
            out.append(f"cp{acp}")
        if oem > 0 and oem != acp:
            out.append(f"cp{oem}")
    except Exception:
        pass
    # Common East-Asian fallbacks when API fails
    out.extend(["gbk", "cp936", "cp950", "big5"])
    return tuple(out)


@lru_cache(maxsize=1)
def candidate_encodings() -> tuple[str, ...]:
    ordered: list[str] = ["utf-8"]
    ordered.extend(_windows_code_pages())
    try:
        pref = locale.getpreferredencoding(False) or ""
        if pref and pref.lower() not in {e.lower() for e in ordered}:
            ordered.append(pref)
    except Exception:
        pass
    # de-dupe case-insensitively, keep order
    seen: set[str] = set()
    uniq: list[str] = []
    for e in ordered:
        key = e.lower().replace("_", "-")
        if key in seen:
            continue
        seen.add(key)
        uniq.append(e)
    return tuple(uniq)


def decode_process_bytes(data: bytes | None | str) -> str:
    """Best-effort decode of process pipe bytes for UI / agent tool results."""
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    if not data:
        return ""
    # UTF-8 BOM
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    # Prefer strict decode so wrong encoding fails instead of silent garbage
    for enc in candidate_encodings():
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        # Heuristic: if "utf-8" produced many U+FFFD, try next
        if enc.lower().replace("_", "-") in ("utf-8", "utf8") and "\ufffd" in text:
            # only reject if replacement ratio is high
            if text.count("\ufffd") >= max(1, len(text) // 20):
                continue
        return text
    return data.decode("utf-8", errors="replace")
