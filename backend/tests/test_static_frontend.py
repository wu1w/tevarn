"""P1-1: committed backend/static without version.json must be treated stale."""

from pathlib import Path

from backend.static_frontend import _static_version_info, is_static_stale


def test_static_dir_without_version_json_is_stale(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TEVARN_ALLOW_STALE_STATIC", "")
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    info = _static_version_info(root)
    assert info["stale"] is True
    assert info["reason"] == "legacy_static_no_version_json"
    assert is_static_stale(root) is True


def test_matching_version_json_is_not_legacy_static(tmp_path: Path, monkeypatch):
    from backend.static_frontend import _product_version

    monkeypatch.setenv("TEVARN_ALLOW_STALE_STATIC", "")
    prod = str(_product_version() or "").strip()
    assert prod
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    (root / "version.json").write_text(
        '{"version": "%s"}' % prod.split("-")[0], encoding="utf-8"
    )
    info = _static_version_info(root)
    assert info["found"]
    assert info["reason"] != "legacy_static_no_version_json"
