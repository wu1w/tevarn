"""Glob brace-set expansion + workspace-root search (nested clone cwd).

Live Windows session (2026-08-13): `**/*.{ts,js,py}` and `**/*.{ts,js,mjs,cjs}`
returned "No files matched." while `**/package.json` and
`tevarn-src/backend/**/*.py` worked. stdlib glob.glob does not expand `{a,b,c}`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.tools.executors import (
    _GLOB_MAX_CHARS,
    _GLOB_MAX_FILES,
    _expand_glob_brace_sets,
    execute_glob,
)


def _norm(p: str) -> str:
    return p.replace("\\", "/")


def _glob_relpaths(out: str) -> set[str]:
    assert not out.startswith("[Error]"), out
    assert not out.startswith("[Security"), out
    if out.startswith("No files matched"):
        return set()
    lines = out.splitlines()
    assert lines and lines[0].startswith("Matched "), out
    return {_norm(line) for line in lines[1:] if line and not line.endswith("…")}


def _write(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _nested_clone_workspace(tmp_path: Path) -> Path:
    """Empty-looking workspace root + cloned subtree, matching the live layout."""
    _write(tmp_path / ".computers" / "main" / "home" / "idle.py")
    src = tmp_path / "tevarn-src"
    _write(src / "backend" / "app.py", "print(1)\n")
    _write(src / "frontend" / "index.ts", "export {}\n")
    _write(src / "frontend" / "util.js", "module.exports = 1\n")
    _write(src / "frontend" / "extra.mjs", "export {}\n")
    _write(src / "package.json", "{}\n")
    _write(src / "README.md", "# x\n")
    return tmp_path


# ── expander unit tests ──────────────────────────────────────────────


def test_expand_glob_brace_sets_suffix_alts():
    assert _expand_glob_brace_sets("**/*.{ts,js,py}") == [
        "**/*.ts",
        "**/*.js",
        "**/*.py",
    ]
    assert _expand_glob_brace_sets("**/*.{ts,js,mjs,cjs}") == [
        "**/*.ts",
        "**/*.js",
        "**/*.mjs",
        "**/*.cjs",
    ]
    # bash: no comma → not a brace-set
    assert _expand_glob_brace_sets("**/*.{ts}") == ["**/*.{ts}"]
    assert _expand_glob_brace_sets("{a,{b,c}}") == ["a", "b", "c"]
    assert _expand_glob_brace_sets("{a,b}{c,d}") == ["ac", "ad", "bc", "bd"]
    assert _expand_glob_brace_sets("plain/*.py") == ["plain/*.py"]


def test_expand_glob_brace_sets_caps_wide_product():
    with pytest.raises(ValueError, match="more than"):
        _expand_glob_brace_sets("{a,b,c,d,e,f,g,h}{a,b,c,d,e,f,g,h}{a,b}")


# ── execute_glob ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_glob_brace_pattern_matches_equivalent_multi_suffix(tmp_path: Path):
    ws = _nested_clone_workspace(tmp_path)
    cfg = {"base_path": str(ws)}
    braced = _glob_relpaths(
        await execute_glob(cfg, {"pattern": "**/*.{ts,js,py}"})
    )
    union: set[str] = set()
    for suffix in ("ts", "js", "py"):
        union |= _glob_relpaths(
            await execute_glob(cfg, {"pattern": f"**/*.{suffix}"})
        )
    assert braced == union
    assert braced == {
        "tevarn-src/backend/app.py",
        "tevarn-src/frontend/index.ts",
        "tevarn-src/frontend/util.js",
    }
    # .computers idle.py must not leak into the coding glob
    assert not any(p.startswith(".computers/") for p in braced)


@pytest.mark.asyncio
async def test_glob_brace_finds_files_under_nested_clone_without_prefix(
    tmp_path: Path,
):
    """Default search root is workspace; do not require tevarn-src/ prefix."""
    ws = _nested_clone_workspace(tmp_path)
    cfg = {"base_path": str(ws)}
    out = await execute_glob(cfg, {"pattern": "**/*.{ts,js,mjs,cjs}"})
    paths = _glob_relpaths(out)
    assert paths == {
        "tevarn-src/frontend/index.ts",
        "tevarn-src/frontend/util.js",
        "tevarn-src/frontend/extra.mjs",
    }
    pkg = _glob_relpaths(await execute_glob(cfg, {"pattern": "**/package.json"}))
    assert pkg == {"tevarn-src/package.json"}


@pytest.mark.asyncio
async def test_glob_windows_mixed_separators_match_nested_paths(tmp_path: Path):
    ws = _nested_clone_workspace(tmp_path)
    cfg = {"base_path": str(ws)}
    mixed = await execute_glob(
        cfg, {"pattern": r"tevarn-src\backend\**\*.py"}
    )
    prefixed = await execute_glob(
        cfg, {"pattern": "tevarn-src/backend/**/*.py"}
    )
    assert _glob_relpaths(mixed) == _glob_relpaths(prefixed) == {
        "tevarn-src/backend/app.py"
    }


@pytest.mark.asyncio
async def test_glob_empty_workspace_skips_computers_only(tmp_path: Path):
    _write(tmp_path / ".computers" / "main" / "home" / "idle.py")
    cfg = {"base_path": str(tmp_path)}
    out = await execute_glob(cfg, {"pattern": "**/*.{ts,js,py,go,rs}"})
    # Hidden `.computers` is not scanned by stdlib glob (`include_hidden` default
    # false); live idle workspace therefore reports a plain miss, not excluded-N.
    assert out == "No files matched."
    assert _glob_relpaths(out) == set()
    # Explicit prefix still respects the heavy-dir skip list.
    explicit = await execute_glob(cfg, {"pattern": ".computers/**/*.py"})
    assert explicit.startswith("No files matched")
    assert "idle.py" not in explicit
    forced = await execute_glob(
        cfg, {"pattern": ".computers/**/*.py", "include_heavy": True}
    )
    assert "idle.py" in _norm(forced)


@pytest.mark.asyncio
async def test_glob_unbraced_pattern_and_caps_unchanged(tmp_path: Path):
    cfg = {"base_path": str(tmp_path)}
    for i in range(_GLOB_MAX_FILES + 10):
        _write(tmp_path / "src" / f"f{i:03d}.txt")
    out = await execute_glob(cfg, {"pattern": "**/*.txt"})
    assert f"Matched {_GLOB_MAX_FILES + 10} file(s)" in out
    assert f"showing ≤{_GLOB_MAX_FILES} paths" in out
    listed = [ln for ln in out.splitlines()[1:] if ln]
    assert len(listed) <= _GLOB_MAX_FILES

    long_dir = tmp_path / ("n" * 200) / ("m" * 200)
    for i in range(40):
        _write(long_dir / f"wide{i:02d}.dat", "y\n")
    wide = await execute_glob(cfg, {"pattern": "**/*.dat"})
    assert "Matched 40 file(s)" in wide
    assert f"≤{_GLOB_MAX_CHARS} chars" in wide or "…" in wide
    # Unbraced nested path still works (live: tevarn-src/backend/**/*.py)
    _write(tmp_path / "tevarn-src" / "backend" / "ok.py")
    py = _glob_relpaths(
        await execute_glob(cfg, {"pattern": "tevarn-src/backend/**/*.py"})
    )
    assert py == {"tevarn-src/backend/ok.py"}


@pytest.mark.asyncio
async def test_glob_sandbox_root_excludes_outside_files(tmp_path: Path):
    ws = tmp_path / "ws"
    outside = tmp_path / "outside"
    _write(ws / "inside.py")
    _write(outside / "secret.py")
    cfg = {"base_path": str(ws)}
    paths = _glob_relpaths(await execute_glob(cfg, {"pattern": "**/*.py"}))
    assert paths == {"inside.py"}
    abs_out = await execute_glob(
        cfg, {"pattern": str(outside / "**" / "*.py").replace("\\", "/")}
    )
    assert "secret.py" not in abs_out
    blocked = await execute_glob(cfg, {"pattern": "../outside/**/*.py"})
    assert blocked.startswith("[Security Blocked]")
