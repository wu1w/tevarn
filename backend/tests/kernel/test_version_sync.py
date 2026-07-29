"""产品版本号一致性：与 CHANGELOG 顶栏 / package 对齐。"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXPECTED = "0.4.6-alpha"


def test_changelog_latest_is_expected() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(r"^## \[([^\]]+)\]", text, re.M)
    assert m, "CHANGELOG missing version header"
    assert m.group(1) == EXPECTED


def test_product_version_files_aligned() -> None:
    pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    fpkg = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert pkg["version"] == EXPECTED
    assert fpkg["version"] == EXPECTED

    app_v = (ROOT / "frontend" / "lib" / "appVersion.ts").read_text(encoding="utf-8")
    assert f"'{EXPECTED}'" in app_v or f'"{EXPECTED}"' in app_v

    assert (ROOT / "backend" / "VERSION").read_text(encoding="utf-8").strip() == EXPECTED

    root_py = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    be_py = (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{EXPECTED}"' in root_py
    assert f'version = "{EXPECTED}"' in be_py
