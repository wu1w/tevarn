"""P1-5/6 + P2 memory/pr helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from takton_code.agent.memory_local import append_memory, clear_memory, read_memory
from takton_code.agent.multimodal import build_user_content, find_image_paths


def test_find_and_build_image_content(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TAKTON_CODE_HOME", str(tmp_path / "home"))
    img = tmp_path / "shot.png"
    # minimal 1x1 PNG
    import base64

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    img.write_bytes(png)
    text = f"look at {img.name} please"
    paths = find_image_paths(text, tmp_path)
    # relative name may not resolve without path — use absolute
    text2 = f"see {img}"
    paths = find_image_paths(text2, tmp_path)
    assert paths and paths[0].name == "shot.png"
    content = build_user_content(text2, tmp_path, enabled=True)
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert any(p.get("type") == "image_url" for p in content if isinstance(p, dict))
    plain = build_user_content(text2, tmp_path, enabled=False)
    assert isinstance(plain, str)


def test_local_memory(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TAKTON_CODE_HOME", str(tmp_path / "h"))
    clear_memory()
    assert read_memory() == ""
    p = append_memory("prefer pytest")
    assert p.is_file()
    assert "pytest" in read_memory()
    clear_memory()
    assert read_memory() == ""
