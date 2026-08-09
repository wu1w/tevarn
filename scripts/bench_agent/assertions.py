"""任务断言：判定一次 agent 运行是否真的完成了任务（T6）。

设计原则：**只认可机器验证的事实**。
不用 LLM 当裁判、不做模糊匹配 —— 那会让 bench 分数随裁判模型漂移，
失去「同一 sha 重复跑得到同一结论」这个唯一有价值的性质。

每条断言拿到运行结束后的 workspace 目录 + agent 最终回复，返回 (ok, detail)。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class AssertionResult:
    type: str
    ok: bool
    detail: str


def _read(workspace: Path, rel: str) -> str | None:
    p = workspace / rel
    if not p.is_file():
        return None
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _a_file_exists(ws: Path, spec: dict, reply: str) -> tuple[bool, str]:
    rel = spec["path"]
    ok = (ws / rel).is_file()
    return ok, f"{rel} {'存在' if ok else '不存在'}"


def _a_file_absent(ws: Path, spec: dict, reply: str) -> tuple[bool, str]:
    rel = spec["path"]
    ok = not (ws / rel).exists()
    return ok, f"{rel} {'已不存在' if ok else '仍存在'}"


def _a_file_contains(ws: Path, spec: dict, reply: str) -> tuple[bool, str]:
    rel = spec["path"]
    text = _read(ws, rel)
    if text is None:
        return False, f"{rel} 不存在或不可读"
    pat = spec["pattern"]
    ok = bool(re.search(pat, text, re.MULTILINE))
    return ok, f"{rel} {'命中' if ok else '未命中'} /{pat}/"


def _a_file_not_contains(ws: Path, spec: dict, reply: str) -> tuple[bool, str]:
    rel = spec["path"]
    text = _read(ws, rel)
    if text is None:
        # 文件不在 = 一定不含
        return True, f"{rel} 不存在（视为不含）"
    pat = spec["pattern"]
    hit = re.search(pat, text, re.MULTILINE)
    return not hit, f"{rel} {'仍含' if hit else '不含'} /{pat}/"


def _a_command(ws: Path, spec: dict, reply: str) -> tuple[bool, str]:
    """在 workspace 里跑命令，比对退出码。

    这是最强的一类断言：测试通过 / 脚本可运行是无法靠「说得好听」蒙混的。

    `{python}` 会被替换成当前解释器的绝对路径 —— 断言是判定基准，必须确定性。
    裸写 `python` 在 macOS（只有 python3）或未激活 venv 时会 127，
    那样失败的是 harness 而不是 agent，分数就没有意义了。
    """
    cmd = str(spec["command"]).replace("{python}", sys.executable or "python3")
    expect = int(spec.get("expect_exit_code", 0))
    timeout = int(spec.get("timeout", 120))
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        r = subprocess.run(
            cmd,
            shell=True,
            cwd=str(ws),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"`{cmd}` 超时 {timeout}s"
    ok = r.returncode == expect
    tail = (r.stdout or "")[-300:] + (r.stderr or "")[-300:]
    return ok, f"`{cmd}` exit={r.returncode} (期望 {expect}) {tail.strip()[:200]}"


def _a_reply_contains(ws: Path, spec: dict, reply: str) -> tuple[bool, str]:
    pat = spec["pattern"]
    ok = bool(re.search(pat, reply or "", re.IGNORECASE | re.MULTILINE))
    return ok, f"回复{'命中' if ok else '未命中'} /{pat}/"


def _a_reply_not_contains(ws: Path, spec: dict, reply: str) -> tuple[bool, str]:
    """用于诚实性检查：agent 不该在做不到时编造成功。"""
    pat = spec["pattern"]
    hit = re.search(pat, reply or "", re.IGNORECASE | re.MULTILINE)
    return not hit, f"回复{'含' if hit else '不含'} /{pat}/"


def _a_workspace_unchanged(ws: Path, spec: dict, reply: str) -> tuple[bool, str]:
    """只读任务用：除白名单外不得有任何文件改动。

    比较对象是 fixture 的原始快照（由 runner 注入 spec['_baseline']）。
    """
    baseline: dict[str, str] = spec.get("_baseline") or {}
    allow = set(spec.get("allow", []))
    changed: list[str] = []
    for p in sorted(ws.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(ws))
        if rel in allow or rel.startswith(".tevarn"):
            continue
        try:
            cur = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if baseline.get(rel) != cur:
            changed.append(rel)
    ok = not changed
    return ok, "无改动" if ok else f"改动了 {', '.join(changed[:5])}"


ASSERTIONS: dict[str, Callable[[Path, dict, str], tuple[bool, str]]] = {
    "file_exists": _a_file_exists,
    "file_absent": _a_file_absent,
    "file_contains": _a_file_contains,
    "file_not_contains": _a_file_not_contains,
    "command": _a_command,
    "reply_contains": _a_reply_contains,
    "reply_not_contains": _a_reply_not_contains,
    "workspace_unchanged": _a_workspace_unchanged,
}


def run_assertions(
    workspace: Path, specs: list[dict[str, Any]], reply: str
) -> list[AssertionResult]:
    out: list[AssertionResult] = []
    for spec in specs:
        kind = str(spec.get("type") or "")
        fn = ASSERTIONS.get(kind)
        if fn is None:
            out.append(AssertionResult(kind or "?", False, f"未知断言类型 '{kind}'"))
            continue
        try:
            ok, detail = fn(workspace, spec, reply)
        except Exception as e:  # 断言自身出错算失败，但不能中断整轮
            ok, detail = False, f"断言执行异常: {e}"
        out.append(AssertionResult(kind, ok, detail))
    return out
