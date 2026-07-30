"""产品版本号一致性：backend/VERSION 与 package manifests 对齐。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
EXPECTED = (ROOT / "backend" / "VERSION").read_text(encoding="utf-8").strip()


def test_product_version_files_aligned() -> None:
    assert EXPECTED, "backend/VERSION empty"
    pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    fpkg = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert pkg["version"] == EXPECTED
    assert fpkg["version"] == EXPECTED

    app_v = (ROOT / "frontend" / "lib" / "appVersion.ts").read_text(encoding="utf-8")
    assert f"'{EXPECTED}'" in app_v or f'"{EXPECTED}"' in app_v

    root_py = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    be_py = (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{EXPECTED}"' in root_py
    assert f'version = "{EXPECTED}"' in be_py


def test_product_version_helper() -> None:
    from backend.core.version import product_version

    assert product_version() == EXPECTED


def test_changelog_optional() -> None:
    """公开仓库可不带 CHANGELOG；若存在则顶栏须对齐 VERSION。"""
    path = ROOT / "CHANGELOG.md"
    if not path.is_file():
        pytest.skip("CHANGELOG.md not published")
    import re

    text = path.read_text(encoding="utf-8")
    m = re.search(r"^## \[([^\]]+)\]", text, re.M)
    assert m, "CHANGELOG missing version header"
    assert m.group(1) == EXPECTED
