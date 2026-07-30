"""P1/P2 夜间路线图新模块测试（2026-07-29）

覆盖：
- evolution/distiller.py：SKILL.md 渲染 / JSON 宽容解析 / 模板兜底入库 / 去重
- evolution/scoreboard.py：计分 / 退化判定 / 自动回滚（降级不删除）
- computer/docker_backend.py：cwd 越界红线 / 容器命名
- computer/ssh_backend.py：未配置报错 / BatchMode 红线
- tools/adapters/mcp_adapter.py：kernel.mediate 收口（mcp_call 拒绝路径）

注意：不触网、不依赖 docker/ssh 真实环境，全部纯逻辑 + tmp sqlite。
"""
from __future__ import annotations

import asyncio

import pytest

# ─────────── fixtures ───────────


@pytest.fixture()
def evo_db(tmp_path, monkeypatch):
    """隔离的 evolution sqlite + store 缓存复位。"""
    db_file = tmp_path / "evo_test.db"
    monkeypatch.setenv("TAKTON_EVOLUTION_DB", str(db_file))

    from backend.evolution import config as evo_config
    from backend.evolution import store

    # 强制重读配置 + schema 重建
    monkeypatch.setattr(store, "_initialized", None)
    if hasattr(evo_config, "_config"):
        monkeypatch.setattr(evo_config, "_config", None, raising=False)
    yield db_file
    monkeypatch.setattr(store, "_initialized", None)


def _run(coro):
    return asyncio.run(coro)


# ─────────── distiller ───────────


def test_render_skill_md_frontmatter():
    from backend.evolution.distiller import render_skill_md

    md = render_skill_md("my_skill", "何时使用一句话", "## 步骤\n1. 做事")
    lines = md.splitlines()
    assert lines[0] == "---"
    assert "name: my_skill" in lines
    assert "description: 何时使用一句话" in lines
    assert "source: takton-evolution" in md
    assert md.rstrip().endswith("1. 做事")


def test_parse_llm_json_tolerates_fences():
    from backend.evolution.distiller import _parse_llm_json

    fenced = '```json\n{"skip": false, "name": "x"}\n```'
    assert _parse_llm_json(fenced) == {"skip": False, "name": "x"}
    noisy = '前置说明 {"a": 1} 尾巴'
    assert _parse_llm_json(noisy) == {"a": 1}
    assert _parse_llm_json("not json") is None
    assert _parse_llm_json("") is None


def test_sanitize_skill_name():
    from backend.evolution.distiller import _sanitize_skill_name

    assert _sanitize_skill_name("My Cool-Skill!") == "my_cool_skill"
    assert _sanitize_skill_name("") == "distilled_skill"
    assert len(_sanitize_skill_name("a" * 200)) <= 64


def test_distill_template_fallback_creates_draft(evo_db, monkeypatch):
    """LLM 不可用 + 失败轨迹 → improver 模板兜底，draft 入库（不直接上线）。"""
    from backend.evolution import distiller, store

    async def _no_llm(*a, **k):
        return None

    monkeypatch.setattr(distiller, "_distill_llm", _no_llm)

    trace = [
        {"name": "file_read", "args": "a.txt", "result": "[Error] not found"},
        {"name": "search", "args": "q", "result": ""},
        {"name": "http", "args": "url", "result": "timeout waiting"},
    ]
    asset = _run(
        distiller.distill_from_trajectory(
            user_input="帮我分析日志文件",
            tool_trace=trace,
            final_content="",
            success=False,
            session_id="s1",
        )
    )
    assert asset is not None
    assert asset["status"] == "draft"          # 红线：只进审批链
    assert asset["kind"] == "skill"
    got = store.get_asset(asset["id"])
    assert got is not None and got["content"].startswith("---")


def test_distill_skips_short_trace(evo_db):
    from backend.evolution.distiller import distill_from_trajectory

    out = _run(
        distill_from_trajectory(
            user_input="hi", tool_trace=[{"name": "a"}], success=True
        )
    )
    assert out is None


def test_distill_dedup_same_name(evo_db, monkeypatch):
    from backend.evolution import distiller

    async def _fixed(*a, **k):
        return {"name": "dup_skill", "description": "d", "body": "x" * 100, "generator": "llm"}

    monkeypatch.setattr(distiller, "_distill_llm", _fixed)
    trace = [{"name": f"t{i}", "result": "ok"} for i in range(4)]

    first = _run(distiller.distill_from_trajectory(user_input="u", tool_trace=trace, success=True))
    second = _run(distiller.distill_from_trajectory(user_input="u", tool_trace=trace, success=True))
    assert first is not None
    assert second is None  # 同名非归档已存在 → 跳过


# ─────────── scoreboard ───────────


def _mk_two_gens(store, name="evo_test_skill"):
    g0 = store.create_asset(kind="skill", name=name, content="v0", status="applied", gen=0)
    g1 = store.create_asset(kind="skill", name=name, content="v1", status="applied", gen=1)
    return g0, g1


def test_scoreboard_insufficient_samples(evo_db):
    from backend.evolution import store
    from backend.evolution.scoreboard import check_regression

    _mk_two_gens(store)
    d = check_regression("evo_test_skill")
    assert d["verdict"] == "insufficient_samples"


def test_scoreboard_regression_and_rollback(evo_db, monkeypatch):
    from backend.evolution import scoreboard, store

    g0, g1 = _mk_two_gens(store)
    # gen0 高成功率，gen1 崩了
    for _ in range(10):
        store.add_skill_outcome(skill_name="evo_test_skill", gen=0, success=True)
    for i in range(10):
        store.add_skill_outcome(skill_name="evo_test_skill", gen=1, success=(i < 2))

    d = scoreboard.check_regression("evo_test_skill")
    assert d["verdict"] == "regressed"
    assert d["drop"] > 0.5

    # 回滚：skills 表同步 mock 掉（不碰主库）
    calls = {}

    async def _fake_upsert(**kw):
        calls.update(kw)
        return {"ok": True}

    import backend.evolution.skill_sync as sync_mod

    monkeypatch.setattr(sync_mod, "upsert_skill_from_asset", _fake_upsert)

    out = _run(scoreboard.maybe_rollback("evo_test_skill"))
    assert out["rollback"]["ok"] is True
    assert out["rollback"]["restored_gen"] == 0
    # 降级不删除
    assert store.get_asset(g1["id"])["status"] == "archived"
    assert store.get_asset(g0["id"])["status"] == "applied"
    assert calls.get("name") == "evo_test_skill"


def test_scoreboard_healthy_no_rollback(evo_db):
    from backend.evolution import store
    from backend.evolution.scoreboard import maybe_rollback

    _mk_two_gens(store, name="healthy_skill")
    for _ in range(10):
        store.add_skill_outcome(skill_name="healthy_skill", gen=0, success=True)
        store.add_skill_outcome(skill_name="healthy_skill", gen=1, success=True)
    out = _run(maybe_rollback("healthy_skill"))
    assert out["verdict"] == "healthy"
    assert "rollback" not in out


def test_scoreboard_record_outcome_ignores_non_evolved(evo_db):
    from backend.evolution import store
    from backend.evolution.scoreboard import record_outcome

    record_outcome(skill_name="not_an_asset", success=True)  # 不应 raise
    assert store.skill_outcome_stats("not_an_asset", 0)["samples"] == 0


# ─────────── docker backend（纯逻辑，不需要 docker）───────────


def test_docker_guest_cwd_containment(tmp_path):
    from backend.computer.docker_backend import DockerBackend

    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    b = DockerBackend(str(ws), "main")
    assert b._guest_cwd(str(ws)) == "/workspace"
    assert b._guest_cwd(str(ws / "sub")) == "/workspace/sub"
    # 越界红线
    assert b._guest_cwd(str(tmp_path)) is None
    assert b._guest_cwd(str(tmp_path / "outside")) is None


def test_docker_container_name_sanitized():
    from backend.computer.docker_backend import _container_name

    assert _container_name("wf:1234/abc") == "takton-agent-wf-1234-abc"
    assert _container_name("") == "takton-agent-main"


# ─────────── ssh backend（纯逻辑，不需要 ssh 服务器）───────────


def test_ssh_not_configured_fails_cleanly(tmp_path):
    from backend.computer.ssh_backend import SshBackend

    b = SshBackend(str(tmp_path), "main", host="")
    r = _run(b.run("echo hi", cwd=str(tmp_path)))
    assert r.exit_code != 0
    assert r.error == "ssh_not_configured" or "未配置" in r.stderr


def test_ssh_argv_has_batchmode(tmp_path):
    from backend.computer.ssh_backend import SshBackend

    b = SshBackend(str(tmp_path), "wf:x", host="user@vps", port=2222)
    argv = b._ssh_argv("echo hi", timeout=60)
    assert "BatchMode=yes" in argv           # 绝不交互挂死
    assert "-p" in argv and "2222" in argv
    assert argv[-2] == "user@vps"
    # agent_key 里的 Windows 非法字符被清洗
    assert ":" not in b._remote_dir().split("/")[-1]


# ─────────── MCP mediate 收口 ───────────


def test_mcp_adapter_mediate_denies_without_capability():
    from backend.kernel import get_kernel
    from backend.kernel.kernel import reset_kernel_for_tests
    from backend.tools.adapters.mcp_adapter import MCPToolAdapter

    reset_kernel_for_tests()
    k = get_kernel()

    async def go():
        proc = await k.create_process("t", capabilities=["file_read"])  # 无 mcp 能力
        adapter = MCPToolAdapter(
            server_name="srv", tool_name="fetch", description="", parameters={}
        )
        out = await adapter.execute(_kernel_process_id=proc.id, url="http://x")
        return out

    out = asyncio.run(go())
    assert isinstance(out, str) and out.startswith("[Error] Kernel 拦截")
    reset_kernel_for_tests()


def test_mcp_adapter_compat_mode_passes_mediate():
    """兼容模式（capabilities=None）→ mediate 放行，走到 client 未连接分支。"""
    from backend.kernel import get_kernel
    from backend.kernel.kernel import reset_kernel_for_tests
    from backend.tools.adapters.mcp_adapter import MCPToolAdapter

    reset_kernel_for_tests()
    k = get_kernel()

    async def go():
        proc = await k.create_process("t")  # 兼容模式
        adapter = MCPToolAdapter(
            server_name="offline-srv", tool_name="fetch", description="", parameters={}
        )
        return await adapter.execute(_kernel_process_id=proc.id)

    out = asyncio.run(go())
    assert "not connected" in out  # 过了 mediate，卡在连接（预期）
    reset_kernel_for_tests()
