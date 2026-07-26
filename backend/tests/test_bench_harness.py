"""T6：bench harness 自身的正确性（不调用 LLM）。

bench 的分数只有在 harness 可信时才有意义，所以要先证明三件事：
  1. 任务真正完成时断言会**变绿**（否则永远 0 分，bench 无用）
  2. 任务没做时断言会**变红**（否则永远 100 分，同样无用）
  3. 靠删测试/跳过测试等取巧手段**骗不过**断言
"""

import shutil
import sys
from pathlib import Path

import pytest
import yaml

BENCH = Path(__file__).resolve().parents[2] / "scripts" / "bench_agent"
sys.path.insert(0, str(BENCH.parents[1]))

from scripts.bench_agent.assertions import run_assertions  # noqa: E402
from scripts.bench_agent.report import compare, summarize  # noqa: E402
from scripts.bench_agent.runner import snapshot_workspace  # noqa: E402

TASKS = sorted((BENCH / "tasks").glob("*.yaml"))
FIXTURES = BENCH / "fixtures"


def _load(name: str) -> dict:
    return yaml.safe_load((BENCH / "tasks" / name).read_text(encoding="utf-8"))


def _ws(tmp_path: Path, fixture: str) -> Path:
    dst = tmp_path / "ws"
    shutil.copytree(FIXTURES / fixture, dst)
    return dst


def _specs(task: dict, ws: Path) -> list[dict]:
    out = []
    for s in task.get("assertions", []):
        s = dict(s)
        if s.get("type") == "workspace_unchanged":
            s["_baseline"] = snapshot_workspace(ws)
        out.append(s)
    return out


# ── 任务集完整性 ────────────────────────────────────────────


def test_task_set_is_complete_and_wellformed():
    assert len(TASKS) >= 20, f"任务数不足: {len(TASKS)}"
    names, cats = set(), set()
    for p in TASKS:
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert d.get("name"), f"{p.name} 缺 name"
        assert d["name"] not in names, f"任务名重复: {d['name']}"
        names.add(d["name"])
        assert d.get("prompt", "").strip(), f"{d['name']} 缺 prompt"
        assert d.get("assertions"), f"{d['name']} 没有断言 —— 无法判定成败"
        cats.add(d.get("category", ""))
        for a in d["assertions"]:
            assert a.get("type"), f"{d['name']} 有断言缺 type"
    # 覆盖面：不能全是同一类任务
    for need in ("fix_bug", "feature", "answer", "long_task", "honesty", "safety"):
        assert need in cats, f"缺少任务类别: {need}"


def test_every_fixture_referenced_exists():
    for p in TASKS:
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        fx = d.get("fixture")
        if fx:
            assert (FIXTURES / fx).is_dir(), f"{d['name']} 引用了不存在的 fixture {fx}"


def test_task_budgets_are_bounded():
    """没有预算上限的任务会让 bench 卡死。"""
    for p in TASKS:
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        it = int((d.get("budget") or {}).get("max_iterations", 0))
        assert 0 < it <= 60, f"{d['name']} 预算不合理: {it}"


# ── 断言在「做对了」时必须变绿 ──────────────────────────────


def test_assertions_go_green_when_task_actually_solved(tmp_path):
    """人工正确修复 fix_bug_01 后，全部断言必须通过。

    这是 harness 最关键的性质：若做对也不给分，分数就毫无意义。
    """
    task = _load("fix_bug_01_keyerror.yaml")
    ws = _ws(tmp_path, task["fixture"])

    auth = ws / "app" / "auth.py"
    src = auth.read_text(encoding="utf-8")
    src = src.replace(
        "    stored = _USERS[user]\n    return stored == hash_password(raw)",
        "    stored = _USERS.get(user)\n"
        "    if stored is None:\n"
        "        return False\n"
        "    return stored == hash_password(raw)",
    )
    auth.write_text(src, encoding="utf-8")

    results = run_assertions(ws, _specs(task, ws), "修好了")
    failed = [f"{r.type}: {r.detail}" for r in results if not r.ok]
    assert not failed, f"正确解法却未通过断言: {failed}"


def test_assertions_go_red_when_untouched(tmp_path):
    task = _load("fix_bug_01_keyerror.yaml")
    ws = _ws(tmp_path, task["fixture"])
    results = run_assertions(ws, _specs(task, ws), "我修好了")
    assert any(not r.ok for r in results), "未修复却全绿 —— 断言失效"


def test_cheating_by_skipping_tests_is_caught(tmp_path):
    """把测试标 skip 能让 pytest 变绿，但防作弊断言必须拦住。"""
    task = _load("fix_bug_01_keyerror.yaml")
    ws = _ws(tmp_path, task["fixture"])

    t = ws / "tests" / "test_auth.py"
    src = t.read_text(encoding="utf-8")
    src = "import pytest\n" + src.replace(
        "def test_unknown_user_does_not_crash():",
        "@pytest.mark.skip(reason='cheat')\ndef test_unknown_user_does_not_crash():",
    )
    t.write_text(src, encoding="utf-8")

    results = run_assertions(ws, _specs(task, ws), "")
    assert any(not r.ok for r in results), "靠 skip 蒙混过关了 —— 防作弊断言失效"


def test_cheating_by_deleting_test_is_caught(tmp_path):
    task = _load("fix_bug_01_keyerror.yaml")
    ws = _ws(tmp_path, task["fixture"])
    t = ws / "tests" / "test_auth.py"
    t.write_text("def test_nothing():\n    pass\n", encoding="utf-8")

    results = run_assertions(ws, _specs(task, ws), "")
    assert any(not r.ok for r in results), "删掉测试后仍算通过 —— 断言失效"


# ── 各断言类型的行为 ────────────────────────────────────────


def test_workspace_unchanged_detects_writes(tmp_path):
    ws = _ws(tmp_path, "config_repo")
    base = snapshot_workspace(ws)
    clean = run_assertions(ws, [{"type": "workspace_unchanged", "_baseline": base}], "")[0]
    assert clean.ok is True

    (ws / "conf" / "app.yaml").write_text("tampered\n", encoding="utf-8")
    r = run_assertions(ws, [{"type": "workspace_unchanged", "_baseline": base}], "")[0]
    assert r.ok is False
    assert "app.yaml" in r.detail


def test_command_assertion_uses_current_interpreter(tmp_path):
    """{python} 必须解析成当前解释器：裸 python 在 macOS/未激活 venv 下会 127，
    那样失败的是 harness 而不是 agent。"""
    ws = tmp_path / "w"
    ws.mkdir()
    r = run_assertions(
        ws, [{"type": "command", "command": "{python} -c \"print(1)\""}], ""
    )[0]
    assert r.ok is True, r.detail


def test_reply_assertions(tmp_path):
    ws = tmp_path / "w"
    ws.mkdir()
    good = run_assertions(ws, [{"type": "reply_contains", "pattern": "320"}], "降到 320ms")[0]
    assert good.ok is True
    bad = run_assertions(ws, [{"type": "reply_not_contains", "pattern": "timeout: 30"}], "timeout: 30")[0]
    assert bad.ok is False


def test_unknown_assertion_type_fails_loudly(tmp_path):
    ws = tmp_path / "w"
    ws.mkdir()
    r = run_assertions(ws, [{"type": "no_such_check"}], "")[0]
    assert r.ok is False
    assert "未知断言类型" in r.detail


# ── 汇总与对比 ──────────────────────────────────────────────


def _run(task, passed, **kw):
    return {
        "task": task, "repeat_index": 0, "passed": passed,
        "iterations": kw.get("iterations", 5), "tool_calls": kw.get("tool_calls", 3),
        "wall_seconds": 1.0, "prompt_tokens": kw.get("prompt_tokens", 1000),
        "cache_read_tokens": kw.get("cache_read_tokens", 0),
        "assertions": [], "reply": "", "error": "",
    }


def test_summarize_computes_pass_rate_and_cache():
    s = summarize([
        _run("a", True, cache_read_tokens=900),
        _run("a", False, cache_read_tokens=900),
        _run("b", True, cache_read_tokens=0),
    ])
    assert s["total_runs"] == 3
    assert s["passed"] == 2
    assert s["tasks"]["a"]["pass_rate"] == 0.5
    assert 0 < s["cache_hit_rate"] < 1


def test_compare_flags_regression():
    base = summarize([_run("a", True), _run("b", True)])
    head = summarize([_run("a", True), _run("b", False)])
    out = compare(base, head)
    assert "变差" in out
    assert "b" in out


def test_compare_flags_improvement():
    base = summarize([_run("a", False)])
    head = summarize([_run("a", True)])
    out = compare(base, head)
    assert "变好" in out
