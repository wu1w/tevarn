from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class FileService:
    """Root-jailed file list/read."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        if not self.root.exists():
            self.root.mkdir(parents=True, exist_ok=True)

    def _safe(self, rel: str) -> Path:
        rel = (rel or ".").replace("\\", "/").lstrip("/")
        target = (self.root / rel).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as e:
            raise PermissionError(f"path outside root: {rel}") from e
        return target

    def list_dir(self, rel: str = ".") -> dict[str, Any]:
        path = self._safe(rel)
        if not path.exists():
            raise FileNotFoundError(rel)
        if not path.is_dir():
            raise NotADirectoryError(rel)
        entries = []
        for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                st = child.stat()
                entries.append(
                    {
                        "name": child.name,
                        "type": "dir" if child.is_dir() else "file",
                        "size": st.st_size if child.is_file() else None,
                        "mtime": int(st.st_mtime),
                    }
                )
            except OSError:
                continue
        return {
            "path": str(path.relative_to(self.root)).replace("\\", "/") or ".",
            "root": str(self.root),
            "entries": entries,
        }

    def read_file(self, rel: str, max_bytes: int = 256_000) -> dict[str, Any]:
        path = self._safe(rel)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(rel)
        data = path.read_bytes()
        truncated = False
        if len(data) > max_bytes:
            data = data[:max_bytes]
            truncated = True
        # text if decodable
        try:
            text = data.decode("utf-8")
            return {
                "path": rel,
                "encoding": "utf-8",
                "content": text,
                "truncated": truncated,
                "size": path.stat().st_size,
            }
        except UnicodeDecodeError:
            import base64

            return {
                "path": rel,
                "encoding": "base64",
                "content": base64.b64encode(data).decode("ascii"),
                "truncated": truncated,
                "size": path.stat().st_size,
            }
