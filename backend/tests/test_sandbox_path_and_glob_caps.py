"""沙箱路径消毒 + glob 排除 heavy 目录。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from backend.computer.pathutil import sanitize_agent_key_for_path


def test_sanitize_wf_colon():
    assert ":" not in sanitize_agent_key_for_path("wf:bf29b574-d86a-4dc9-ada1-0f5d0798bbda")
    assert sanitize_agent_key_for_path("wf:abc").startswith("wf_")
    assert sanitize_agent_key_for_path("main") == "main"
    assert "/" not in sanitize_agent_key_for_path("a/b\\c")


def test_job_backend_home_no_colon(tmp_path: Path):
    from backend.computer.job_backend import JobBackend

    key = "wf:deadbeef-1234-5678-9abc-def012345678"
    b = JobBackend(str(tmp_path), key)
    # 驱动器盘符 C: 合法；agent 段不得含冒号
    rel = os.path.relpath(b.agent_home, str(tmp_path))
    assert "wf:" not in rel.replace("/", "\\")
    assert "wf_" in rel or "deadbeef" in rel
    b._ensure_dirs()
    assert Path(b.agent_home).is_dir()


def test_glob_skips_node_modules(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x", encoding="utf-8")
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("y", encoding="utf-8")

    async def go():
        from backend.services.tools.executors import execute_glob

        out = await execute_glob({"base_path": str(tmp_path)}, {"pattern": "**/*"})
        assert "src" in out or "a.py" in out
        assert "node_modules" not in out or "skipped" in out.lower()
        # force include
        out2 = await execute_glob(
            {"base_path": str(tmp_path)},
            {"pattern": "**/*", "include_heavy": True},
        )
        assert "node_modules" in out2 or "index.js" in out2

    asyncio.run(go())
