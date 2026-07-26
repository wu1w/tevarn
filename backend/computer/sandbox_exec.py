"""动态 skill 的 Python 代码沙箱包装（阶段 2：动态生成物默认强隔离）。

`WorkflowEngine._run_code_in_subprocess` 原有防护：AST 白名单校验 +
子进程 + 超时 + 输出截断。这对「工作流里人写的节点」够用，
但对「agent 动态生成的 skill」不够——AST 白名单是黑名单思维的反面教材，
漏一个属性链就是逃逸。

这里在子进程之外再套一层 bwrap（Linux）：
- `--unshare-net` 断网（skill 要联网应走 http handler，那里有 SSRF 校验）
- `--clearenv` 环境清零（不泄漏 API key）
- 文件系统：只读系统目录 + 可写 workspace + 隔离 HOME
- `--die-with-parent` 防孤儿

bwrap 不可用时的策略由调用方决定（auto=回退原防护并告警 /
required=拒绝执行）。本模块只回答两个问题：能不能沙箱、怎么包装 argv。
"""

from __future__ import annotations

import logging
import os
import shutil
import sys

logger = logging.getLogger(__name__)

_RO_BINDS = ("/usr", "/lib", "/lib64", "/bin", "/etc/resolv.conf", "/etc/ssl")


def skill_sandbox_available(bwrap_path: str = "bwrap") -> bool:
    """Linux + bwrap 可用即真。"""
    return sys.platform.startswith("linux") and shutil.which(bwrap_path) is not None


def wrap_python_argv_sandboxed(
    argv: list[str],
    *,
    workspace_root: str,
    agent_key: str = "skills",
    bwrap_path: str = "bwrap",
    script_paths: list[str] | None = None,
) -> list[str]:
    """把 python 执行 argv 包装进 bwrap。

    argv: 原 [python, script, ...]；script_paths: 需单独 ro-bind 进沙箱的脚本
    （/tmp 会被 tmpfs 遮蔽，临时脚本必须显式 bind）。
    """
    workspace_root = os.path.abspath(workspace_root)
    agent_home = os.path.join(workspace_root, ".computers", agent_key, "home")
    os.makedirs(agent_home, exist_ok=True)

    out = [bwrap_path, "--die-with-parent", "--new-session", "--unshare-net"]
    for d in _RO_BINDS:
        if os.path.exists(d):
            out += ["--ro-bind", d, d]
    # Python 解释器真实路径（venv 里是 symlink 链，中间节点在沙箱内不存在，
    # 必须用最终 realpath 执行并 ro-bind 其安装根）
    real_python = os.path.realpath(argv[0])
    python_root = os.path.dirname(os.path.dirname(real_python))
    if os.path.isdir(python_root) and not python_root.startswith(("/usr", "/bin")):
        out += ["--ro-bind", python_root, python_root]
    out += ["--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev"]
    out += ["--bind", workspace_root, workspace_root]
    out += ["--bind", agent_home, agent_home]
    for sp in script_paths or []:
        if os.path.isfile(sp):
            out += ["--ro-bind", sp, sp]
    out += ["--clearenv"]
    out += ["--setenv", "HOME", agent_home]
    out += ["--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin"]
    out += ["--setenv", "LANG", "C.UTF-8"]
    out += ["--chdir", workspace_root]
    out += ["--", real_python, *argv[1:]]
    return out
