"""Detect local image paths in user text and build OpenAI-style multimodal content."""

from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path
from typing import Any

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
# bare paths or @paths ending with image ext
_PATH_RE = re.compile(
    r"""(?P<p>"""
    r"""(?:[A-Za-z]:[\\/]|\\\\|/|\./|\.\./)?"""
    r"""[^\s"'<>|*?]+\.(?:png|jpe?g|gif|webp|bmp)"""
    r""")""",
    re.IGNORECASE,
)


def find_image_paths(text: str, project_root: Path) -> list[Path]:
    root = project_root.resolve()
    found: list[Path] = []
    seen: set[str] = set()
    for m in _PATH_RE.finditer(text or ""):
        raw = m.group("p").strip().strip("\"'")
        if raw.startswith("@"):
            raw = raw[1:]
        candidates = []
        p = Path(raw)
        if p.is_file():
            candidates.append(p.resolve())
        else:
            rel = (root / raw).resolve()
            if rel.is_file():
                candidates.append(rel)
        for c in candidates:
            key = str(c)
            if key in seen:
                continue
            if c.suffix.lower() not in _IMAGE_EXTS:
                continue
            if c.stat().st_size > 8_000_000:
                continue
            seen.add(key)
            found.append(c)
    return found


def file_to_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    data = path.read_bytes()
    b64 = base64.standard_b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def build_user_content(
    text: str,
    project_root: Path,
    *,
    enabled: bool = True,
    max_images: int = 4,
) -> str | list[dict[str, Any]]:
    """
    If local image paths appear in text and enabled, return multimodal content parts.
    Otherwise return plain text.
    """
    if not enabled or not text:
        return text
    paths = find_image_paths(text, project_root)[:max_images]
    if not paths:
        return text
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for p in paths:
        try:
            url = file_to_data_url(p)
        except OSError:
            continue
        parts.append({"type": "image_url", "image_url": {"url": url}})
    if len(parts) == 1:
        return text
    return parts


def content_for_storage(content: str | list[dict[str, Any]]) -> str:
    """Persist multimodal as short text + image path markers (not full base64)."""
    if isinstance(content, str):
        return content
    texts = []
    imgs = []
    for p in content:
        if not isinstance(p, dict):
            continue
        if p.get("type") == "text":
            texts.append(str(p.get("text") or ""))
        elif p.get("type") == "image_url":
            imgs.append("[image attached]")
    body = "\n".join(texts)
    if imgs:
        body = (body + "\n" + " ".join(imgs)).strip()
    return body
