"""轻量密钥脱敏：聊天落库 / 摘要 / 工具结果展示。

不做语义分析，只按常见 token 形态与 key= 赋值模式替换，避免把 Key 回显给用户。
"""

from __future__ import annotations

import re
from typing import Any

# sk- / xai- / ghp_ 等；长十六进制/字母数字串
_TOKEN_RE = re.compile(
    r"(?i)\b("
    r"sk-[A-Za-z0-9_\-]{16,}"
    r"|xai-[A-Za-z0-9_\-]{16,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|gho_[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|Bearer\s+[A-Za-z0-9\-._~+/]+=*"
    r")\b"
)

# key / token / secret / api_key 赋值
_ASSIGN_RE = re.compile(
    r"(?i)\b("
    r"api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"secret|token|password|passwd|authorization|key"
    r")\b(\s*[:=]\s*)([^\s,;\"']{12,})"
)

# 裸长串（像用户粘贴的豆包 key）
_BARE_LONG_RE = re.compile(r"\b([A-Za-z0-9]{28,})\b")


def mask_token(raw: str, *, keep_head: int = 4, keep_tail: int = 4) -> str:
    s = (raw or "").strip()
    if len(s) <= keep_head + keep_tail + 2:
        return "***"
    return f"{s[:keep_head]}…{s[-keep_tail:]}"


def redact_secrets(text: str | None) -> str:
    """对文本中的密钥形态做脱敏。"""
    if not text:
        return ""
    out = str(text)

    def _tok(m: re.Match[str]) -> str:
        return mask_token(m.group(0))

    out = _TOKEN_RE.sub(_tok, out)

    def _assign(m: re.Match[str]) -> str:
        return f"{m.group(1)}{m.group(2)}{mask_token(m.group(3))}"

    out = _ASSIGN_RE.sub(_assign, out)

    # 仅对「配…key」语境中的长串脱敏，避免误伤普通 ID
    if re.search(r"(?i)(api\s*key|密钥|secret|token|配.{0,12}api)", out):
        def _bare(m: re.Match[str]) -> str:
            s = m.group(1)
            # UUID 形态跳过
            if re.fullmatch(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                s,
            ):
                return s
            return mask_token(s)

        out = _BARE_LONG_RE.sub(_bare, out)
    return out


def redact_obj(value: Any, *, depth: int = 0) -> Any:
    """递归脱敏 dict/list 中的敏感字符串字段。"""
    if depth > 6:
        return value
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            ks = str(k).lower()
            if any(
                x in ks
                for x in (
                    "api_key",
                    "apikey",
                    "secret",
                    "token",
                    "password",
                    "authorization",
                    "refresh_token",
                    "access_token",
                )
            ) and isinstance(v, str) and v.strip():
                out[k] = mask_token(v)
            else:
                out[k] = redact_obj(v, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [redact_obj(x, depth=depth + 1) for x in value]
    return value
