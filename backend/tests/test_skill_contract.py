"""Phase 1 Skill 契约（skill.yaml）测试

覆盖：
- 契约解析：完整/最小/非法 YAML/非法字段
- requires 缺失检测（bin / python 模块）
- loader 集成：包目录 skill.yaml → manifest.contract；workflow 渲染进 snippet
- tools 白名单：并集解析 + loop 执行边界真实拦截
"""
import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

# ═══════════ 1. 契约解析 ═══════════

def test_parse_contract_full():
    from backend.skills.contract import parse_contract

    c, errs = parse_contract({
        "name": "code-review",
        "version": "0.2.0",
        "requires": {"bins": ["git"], "python": ["yaml"]},
        "tools": ["file_read", "shell"],
        "permissions": {"fs": "workspace", "network": False},
        "workflow": ["看 diff", "逐项审查"],
    })
    assert errs == []
    assert c.name == "code-review"
    assert c.requires.bins == ["git"]
    assert c.tools == ["file_read", "shell"]
    assert c.permissions.fs == "workspace"
    assert c.workflow == ["看 diff", "逐项审查"]


def test_parse_contract_minimal_defaults():
    from backend.skills.contract import parse_contract

    c, errs = parse_contract({})
    assert errs == []
    assert c.version == "0.1.0"
    assert c.tools == []
    assert c.permissions.fs == "workspace"
    assert c.permissions.network is False


def test_parse_contract_invalid():
    from backend.skills.contract import parse_contract

    c, errs = parse_contract({"permissions": {"fs": "root"}})
    assert c is None and errs  # 非法枚举值
    c2, errs2 = parse_contract(["not-a-mapping"])
    assert c2 is None and errs2


def test_load_contract_for_dir(tmp_path):
    from backend.skills.contract import load_contract_for_dir

    (tmp_path / "skill.yaml").write_text(
        "name: ''\ntools: [shell]\nworkflow: [第一步, 第二步]\n", encoding="utf-8"
    )
    c, errs = load_contract_for_dir(tmp_path)
    assert errs == []
    assert c.name == tmp_path.name  # 空 name 回落目录名
    assert c.tools == ["shell"]

    # 无契约文件
    empty = tmp_path / "other"
    empty.mkdir()
    c2, errs2 = load_contract_for_dir(empty)
    assert c2 is None and errs2 == []


def test_check_requires():
    from backend.skills.contract import SkillContract, check_requires

    c = SkillContract.model_validate({
        "requires": {"bins": ["definitely-not-a-real-bin-xyz"], "python": ["no_such_module_xyz"]}
    })
    missing = check_requires(c)
    assert "bin: definitely-not-a-real-bin-xyz" in missing
    assert "python: no_such_module_xyz" in missing
    # 存在的项不报（用 python 解释器路径语义：bins 用当前平台必有的命令）
    import shutil
    import sys

    bin_ok = "python" if shutil.which("python") else (sys.executable.split("\\")[-1].split("/")[-1] or "python")
    # python 模块 os 必有；bins 用 shutil.which 能找到的
    which_bin = "python" if shutil.which("python") else ("py" if shutil.which("py") else None)
    if which_bin is None:
        # 极端环境：只断言 python 模块检查不误报
        c2 = SkillContract.model_validate({"requires": {"bins": [], "python": ["os"]}})
    else:
        c2 = SkillContract.model_validate({"requires": {"bins": [which_bin], "python": ["os"]}})
    assert check_requires(c2) == []


# ═══════════ 2. loader 集成 ═══════════

def test_loader_attaches_contract_and_renders_workflow(tmp_path):
    from backend.packages.loader import (
        load_workspace_packages,
        resolve_attached_snippets,
        resolve_attached_tool_whitelist,
    )

    pkg = tmp_path / "review-pack"
    pkg.mkdir()
    (pkg / "package.json").write_text(
        '{"name": "review-pack", "system_snippet": "你是审查员"}', encoding="utf-8"
    )
    (pkg / "skill.yaml").write_text(
        "tools: [file_read, shell]\nworkflow:\n  - 看 diff\n  - 按清单审查\n",
        encoding="utf-8",
    )

    with patch("backend.packages.loader.package_search_roots", return_value=[tmp_path]):
        pkgs = load_workspace_packages()
        assert len(pkgs) == 1
        m = pkgs[0]
        assert m.contract is not None
        assert m.contract["tools"] == ["file_read", "shell"]

        # snippet 渲染 workflow
        async def _snip():
            with patch(
                "backend.packages.loader.list_all_packages",
                new=AsyncMock(return_value=pkgs),
            ):
                snippets = await resolve_attached_snippets(["review-pack"])
                wl = await resolve_attached_tool_whitelist(["review-pack"])
                return snippets, wl

        snippets, wl = asyncio.run(_snip())
        assert "你是审查员" in snippets[0]["content"]
        assert "1. 看 diff" in snippets[0]["content"]
        assert "2. 按清单审查" in snippets[0]["content"]
        assert wl == {"file_read", "shell"}

        # 无挂载 → None（不过滤）
        async def _none():
            with patch(
                "backend.packages.loader.list_all_packages",
                new=AsyncMock(return_value=pkgs),
            ):
                return await resolve_attached_tool_whitelist([])

        assert asyncio.run(_none()) is None


# ═══════════ 3. 执行边界拦截 ═══════════

def test_loop_contract_whitelist_blocks_tool():
    from backend.agent import loop as loop_mod

    agent = loop_mod.NexusAgentLoop(
        session_repo=SimpleNamespace(),
        message_repo=SimpleNamespace(),
        task_repo=SimpleNamespace(),
        ctx_item_repo=SimpleNamespace(),
        context_flow_repo=SimpleNamespace(),
        ws_manager=None,
        user_id=None,
        notification_repo=SimpleNamespace(),
    )

    sid = str(uuid.uuid4())
    args = {"_session_id": sid}

    async def _run():
        with patch(
            "backend.packages.session_packages.get_session_attached_packages",
            new=AsyncMock(return_value=["review-pack"]),
        ), patch(
            "backend.packages.loader.resolve_attached_tool_whitelist",
            new=AsyncMock(return_value={"file_read", "shell"}),
        ):
            blocked = await agent._contract_tool_block_reason("git_ops", dict(args))
            allowed = await agent._contract_tool_block_reason("shell", dict(args))
            return blocked, allowed

    blocked, allowed = asyncio.run(_run())
    assert blocked is not None and "Skill Contract Blocked" in blocked
    assert "git_ops" in blocked
    assert allowed is None


def test_loop_contract_no_declaration_no_filter():
    from backend.agent import loop as loop_mod

    agent = loop_mod.NexusAgentLoop(
        session_repo=SimpleNamespace(),
        message_repo=SimpleNamespace(),
        task_repo=SimpleNamespace(),
        ctx_item_repo=SimpleNamespace(),
        context_flow_repo=SimpleNamespace(),
        ws_manager=None,
        user_id=None,
        notification_repo=SimpleNamespace(),
    )

    async def _run():
        with patch(
            "backend.packages.session_packages.get_session_attached_packages",
            new=AsyncMock(return_value=[]),
        ), patch(
            "backend.packages.loader.resolve_attached_tool_whitelist",
            new=AsyncMock(return_value=None),
        ):
            return await agent._contract_tool_block_reason("anything", {"_session_id": "x"})

    assert asyncio.run(_run()) is None
