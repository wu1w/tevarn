"""本轮扫描出的运行时崩溃 bug 的回归测试。

三处都是 pyflakes 可静态发现的 NameError，全部位于错误/后台路径 ——
正常流程覆盖不到，所以长期没被发现。
"""

import inspect

import pytest


def test_websocket_auth_failure_paths_have_no_undefined_self():
    """websocket_endpoint 是模块级函数，不能引用 self。

    此前 6 条认证失败路径写的是 `self._safe_close(...)`，
    使「空 auth 消息 / token 过期 / token 非法 / 会话过期 / 无权访问」
    全部抛 NameError，连接不被干净关闭，异常反冒到 ASGI 层。
    """
    from backend.api import websocket as ws_mod

    src = inspect.getsource(ws_mod.websocket_endpoint)
    assert "self." not in src, "模块级 endpoint 里出现了 self.，必然 NameError"
    assert "safe_close_ws(" in src


@pytest.mark.asyncio
async def test_safe_close_ws_swallows_double_close():
    """重复关闭不得抛错（幂等），其他 RuntimeError 仍需上抛。"""
    from backend.api.websocket import safe_close_ws

    class _AlreadyClosed:
        async def close(self, code=1000, reason=""):
            raise RuntimeError("close message has been sent")

    await safe_close_ws(_AlreadyClosed(), code=1008, reason="x")  # 不抛

    class _Other:
        async def close(self, code=1000, reason=""):
            raise RuntimeError("something else")

    with pytest.raises(RuntimeError, match="something else"):
        await safe_close_ws(_Other())


@pytest.mark.asyncio
async def test_safe_close_ws_is_used_by_manager_method():
    """ConnectionManager._safe_close 与模块级实现保持同一行为。"""
    from backend.api.websocket import ConnectionManager

    calls = []

    class _WS:
        async def close(self, code=1000, reason=""):
            calls.append((code, reason))

    await ConnectionManager()._safe_close(_WS(), code=1008, reason="bye")
    assert calls == [(1008, "bye")]


def test_knowledge_route_has_logger():
    """rebuild_index 等后台任务引用 logger 却未定义 —— 该路径必然 NameError，
    且因跑在 BackgroundTasks 里被静默吞掉，表现为「重建索引点了没反应」。"""
    from backend.api.routes import knowledge

    assert hasattr(knowledge, "logger")
    src = inspect.getsource(knowledge)
    assert "logger.info" in src or "logger.error" in src


def test_cron_hook_workflow_trigger_has_no_undefined_name():
    """trigger_hook 里原写 hook.workflow_id，但本函数中该名字不存在（是 obj），
    导致 target_type='workflow' 的钩子每次触发都 NameError，工作流从未真正执行。"""
    from backend.api.routes import cron_hook

    src = inspect.getsource(cron_hook.trigger_hook)
    # 只看真正的代码行：注释里提到旧写法是为了留档，不该让断言误判
    code = "\n".join(
        ln for ln in src.splitlines() if not ln.lstrip().startswith("#")
    )
    assert "hook.workflow_id" not in code
    assert "workflow_id=str(wf.id)" in code


def test_find_git_root_accepts_file_path(tmp_path):
    """传文件路径时曾把文件当 subprocess 的 cwd，抛 NotADirectoryError。"""
    from backend.project.worktree import find_git_root

    repo = tmp_path / "r"
    (repo / ".git").mkdir(parents=True)
    f = repo / "pkg" / "m.py"
    f.parent.mkdir(parents=True)
    f.write_text("x\n", encoding="utf-8")

    assert find_git_root(f) == repo.resolve()
    assert find_git_root(f.parent) == repo.resolve()


def test_find_git_root_outside_repo_returns_none(tmp_path):
    """非仓库路径返回 None，且不得抛裸 OSError。"""
    from backend.project.worktree import find_git_root

    d = tmp_path / "plain"
    d.mkdir()
    assert find_git_root(d) is None
    assert find_git_root(d / "missing.py") is None
