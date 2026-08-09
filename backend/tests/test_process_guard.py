# -*- coding: utf-8 -*-
"""process_guard + win_boot smoke (no live network)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from backend.core import process_guard as pg


def test_write_breadcrumb_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("TEVARN_HOME", str(tmp_path))
    # reset path cache by calling write
    pg.write_breadcrumb("unit_test_event", foo=1)
    path = pg.breadcrumb_path()
    assert path.is_file()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert lines
    rec = json.loads(lines[-1])
    assert rec["event"] == "unit_test_event"
    assert rec["foo"] == 1
    assert "pid" in rec


def test_memory_pressure_returns_known_label():
    label = pg.memory_pressure()
    assert label in ("ok", "elevated", "critical")


def test_sample_rss_mb_nonneg():
    rss = pg.sample_rss_mb()
    # May be None on exotic platforms; if present must be positive
    if rss is not None:
        assert rss > 0


def test_win_boot_module_importable():
    import backend.win_boot as wb

    assert callable(wb.main)
    assert callable(wb._arm_selector_policy)


def test_isolate_spawn_lock_helpers():
    from backend.services.llm import codex_sse_isolate as iso

    assert callable(iso._win_creationflags)
    if sys.platform == "win32":
        flags = iso._win_creationflags()
        assert flags != 0
        # CREATE_NEW_PROCESS_GROUP bit
        assert flags & 0x00000200
