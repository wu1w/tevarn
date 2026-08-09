"""P0.1：command 中的 python 强制重写为项目 venv 解释器。"""

from __future__ import annotations

from pathlib import Path

from backend.core.project_python import (
    project_root,
    resolve_project_python,
    rewrite_command_python,
)


def test_resolve_points_at_project_venv() -> None:
    py = Path(resolve_project_python())
    assert py.is_file()
    root = project_root().resolve()
    # 优先落在仓库 .venv / venv 或 TEVARN_PYTHON
    text = str(py).lower()
    assert str(root).lower() in text or py.name.lower().startswith("python")


def test_rewrite_bare_python() -> None:
    new, changed = rewrite_command_python('python -c "import sqlalchemy"')
    assert changed is True
    assert "python -c" not in new.lower() or resolve_project_python().lower() in new.lower()
    assert resolve_project_python() in new or resolve_project_python().replace("\\", "/") in new.replace("\\", "/")
    assert "import sqlalchemy" in new


def test_rewrite_python3_and_chained() -> None:
    new, changed = rewrite_command_python("python3 -m pip list && python -V")
    assert changed is True
    py = resolve_project_python()
    # 两处均被替换
    assert new.count(py) >= 2 or new.count(f'"{py}"') >= 1


def test_rewrite_empty_unchanged() -> None:
    new, changed = rewrite_command_python("")
    assert changed is False
    assert new == ""


def test_rewrite_non_python_unchanged() -> None:
    cmd = "node -e \"console.log(1)\""
    new, changed = rewrite_command_python(cmd)
    assert changed is False
    assert new == cmd


def test_rewrite_respects_tevarn_python_env(monkeypatch, tmp_path: Path) -> None:
    fake = tmp_path / "custom-python.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setenv("TEVARN_PYTHON", str(fake))
    assert Path(resolve_project_python()).resolve() == fake.resolve()
    new, changed = rewrite_command_python("python -c pass")
    assert changed is True
    assert str(fake.resolve()) in new or f'"{fake.resolve()}"' in new
