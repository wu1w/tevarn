"""Chat artifact list must not include process files / scratch scripts."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "frontend" / "lib" / "artifacts.ts").read_text(encoding="utf-8")


def test_artifact_extractor_rejects_runtime_and_scratch():
    assert "isInternalRuntimePath" in SRC
    assert "isScratchOrProcessFile" in SRC
    assert ".tevarn" in SRC
    assert "file-history" in SRC
    assert "process_snapshots" in SRC
    assert "tool_results" in SRC
    assert "name.includes('write')" not in SRC
    assert "role !== 'assistant'" in SRC or 'role !== "assistant"' in SRC
    assert "basename(raw.replace" not in SRC


def test_selftest_covers_process_file_leak():
    st = (ROOT / "frontend" / "lib" / "artifacts.selftest.ts").read_text(encoding="utf-8")
    assert "file-history" in st
    assert "file_read" in st
    assert "no process/scratch" in st
    assert "tool-role dumps" in st
