"""Smoke tests for HAEE/evolution + SFT collector."""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(r"E:/项目/taktonl-0.1.0")
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

HOME = ROOT / "data" / "smoke-haee-sft"
if HOME.exists():
    shutil.rmtree(HOME, ignore_errors=True)
HOME.mkdir(parents=True, exist_ok=True)

os.environ["TAKTON_HOME"] = str(HOME.resolve())
os.environ["TAKTON_JWT_SECRET"] = "smoke-jwt-secret-not-default-xx"
os.environ["TAKTON_API_KEY"] = "smoke-api-key-not-default-yy"
os.environ["TAKTON_SETTINGS_ENCRYPTION_SALT"] = "smokesalt12"
os.environ["TAKTON_EVOLUTION_ENABLED"] = "1"
os.environ["TAKTON_EVOLUTION_AUTO_APPLY"] = "1"
os.environ.pop("TAKTON_SFT_USAGE_LOG_ENABLED", None)
os.environ["TAKTON_SFT_CORPUS_DIR"] = str((HOME / "sft_corpus").resolve())

import backend.evolution.config as ec

ec._config = None

from backend.evolution import store
from backend.evolution.manager import get_evolution_manager
from backend.evolution.gates import run_gates
from backend.evolution.runtime_tools import unregister_evolved_tool
from backend.tools.registry import ToolRegistry
from backend.services import sft_collector as sc

sc._enabled_cache = None
sc.invalidate_enabled_cache()

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS {name}")
    else:
        print(f"  FAIL {name} {detail}")
        failures.append(name)


async def main() -> int:
    print("=== A. Evolution core ===")
    m = get_evolution_manager()
    m.ensure_seeded()
    st = m.status()
    check("status_enabled", st.get("enabled") is True)
    check("seed_tasks_ge4", st.get("tasks", 0) >= 4, str(st.get("tasks")))

    r = await m.run_task("evolution-module")
    check("task_evolution_module", float(r.get("score") or 0) >= 0.99, str(r)[:200])
    r2 = await m.run_task("remote-device-optional")
    check("task_remote_optional_skip", float(r2.get("score") or 0) >= 0.99, str(r2)[:200])

    ToolRegistry.clear()
    m.record_tool(
        "sess-smoke",
        name="weather",
        arguments={"city": "上海"},
        result='{"t":20}',
        ok=True,
    )
    turn = await m.on_turn_final(
        "sess-smoke",
        user_input="今天上海天气如何",
        final_content="答案1：晴\n答案2：多云\n答案3：雨",
    )
    check("turn_applied", bool(turn and turn.get("applied")), str(turn)[:240] if turn else "None")
    aname = turn["asset"]["name"] if turn and turn.get("asset") else None
    tool = ToolRegistry.get(aname) if aname else None
    check("tool_registered", tool is not None, str(aname))
    if tool:
        out = await tool.execute(query="再问一次")
        check(
            "tool_execute",
            "playbook" in str(out).lower() or (aname or "") in str(out),
            str(out)[:120],
        )
        assets = store.list_assets(source="auto")
        check(
            "use_count_ge1",
            any(x["name"] == aname and x["use_count"] >= 1 for x in assets),
            str([(x["name"], x["use_count"]) for x in assets[:8]]),
        )

    g = run_gates(name="x", content="api_key=sk-abcdefghijklmnop", summary="bad")
    check("gate_blocks_secret", g["ok"] is False, str(g.get("reasons")))

    if turn and turn.get("asset"):
        aid = turn["asset"]["id"]
        ok = store.delete_asset(aid)
        check("delete_auto_asset", ok)
        unregister_evolved_tool(aname or "")
        check("unregistered", ToolRegistry.get(aname) is None)

    print("=== B. SFT collector ===")
    sc.invalidate_enabled_cache()
    en = await sc.is_enabled()
    check("sft_default_off", en is False)
    r = await sc.collect_if_enabled(
        session_id="s", user_input="u", assistant_output="a", tools=[]
    )
    check("sft_noop_when_off", r is None)

    os.environ["TAKTON_SFT_USAGE_LOG_ENABLED"] = "1"
    sc.invalidate_enabled_cache()
    r = await sc.collect_if_enabled(
        session_id="s1",
        user_input="帮我查天气",
        assistant_output="今天晴。",
        tools=[
            {
                "name": "weather",
                "arguments": {"city": "上海"},
                "result": "ok",
                "ok": True,
            }
        ],
    )
    check("sft_writes", r is not None and Path(r["md_path"]).exists(), str(r))
    if r:
        md = Path(r["md_path"]).read_text(encoding="utf-8")
        check("sft_md_has_user", "帮我查天气" in md)
        check("sft_md_has_assistant", "### assistant" in md)
        check("sft_jsonl_exists", Path(r["jsonl_path"]).exists())
        r3 = await sc.collect_if_enabled(
            session_id="s2",
            user_input="token=ghp_ABCDEFGHIJKLMNOPQRSTUV",
            assistant_output="done",
            tools=[],
        )
        md2 = Path(r3["md_path"]).read_text(encoding="utf-8")
        check(
            "sft_redact",
            "ghp_ABCDEFGHIJKLMNOPQRSTUV" not in md2 and "token=***" in md2.replace(" ", ""),
            md2[-300:],
        )

    print("=== C. Routes ===")
    try:
        from backend.api.routes import evolution as evo_routes
        from backend.api.routes import settings as settings_routes

        paths = [getattr(r, "path", str(r)) for r in evo_routes.router.routes]
        check("evo_routes", any("assets" in p for p in paths), str(paths)[:200])
        sroutes = list(settings_routes.router.routes)
        spaths = [getattr(r, "path", str(r)) for r in sroutes]
        check("sft_corpus_route", any("sft-corpus" in p for p in spaths), str(spaths)[:300])
        sft_i = next(i for i, p in enumerate(spaths) if "sft-corpus" in p)
        key_get_i = None
        for i, r in enumerate(sroutes):
            pth = getattr(r, "path", "")
            methods = getattr(r, "methods", set()) or set()
            if "{key}" in pth and "GET" in methods:
                key_get_i = i
                break
        if key_get_i is not None:
            check("sft_before_key_route", sft_i < key_get_i, f"sft={sft_i} key={key_get_i}")
        else:
            check("sft_before_key_route", True, "no GET {key}")
    except Exception as e:
        check("route_import", False, str(e))

    print("=== D. Regression / old bugs scan ===")
    src = (ROOT / "backend/api/routes/settings.py").read_text(encoding="utf-8")
    check("category_of_sft_keys", "sft_usage_log_enabled" in src)
    loop = (ROOT / "backend/agent/loop.py").read_text(encoding="utf-8")
    check("loop_sft_buffer", "_sft_tools" in loop)
    check("loop_sft_collect", "collect_if_enabled" in loop)
    check("loop_evo_hook", "on_turn_final" in loop)
    check("multi_source_aggregate", "_maybe_aggregate_multi_source" in loop)
    ui = (ROOT / "frontend/app/settings/page.tsx").read_text(encoding="utf-8")
    check("ui_no_double_comma", ",," not in ui)
    check("ui_sft_section", "收集使用日志" in ui and "sftHelpOpen" in ui)
    evo_ui = (ROOT / "frontend/app/evolution/page.tsx").read_text(encoding="utf-8")
    check("evo_page_exists", "use_count" in evo_ui and "自主进化" in evo_ui)
    side = (ROOT / "frontend/components/layout/Sidebar.tsx").read_text(encoding="utf-8")
    line = [ln for ln in side.splitlines() if "自主进化" in ln][0]
    check("no_badge_on_evolution_nav", "badge" not in line.lower())

    seeds = store.list_assets(source="seed")
    if seeds:
        check("seed_not_deletable", store.delete_asset(seeds[0]["id"]) is False)
    else:
        check("seed_assets_exist", False, "no seed assets")

    # bulk delete unused shouldn't remove active-used only - create unused draft
    store.create_asset(
        kind="skill",
        name="evo_unused_tmp",
        summary="tmp",
        source="auto",
        status="draft",
        content="x",
    )
    n = store.bulk_delete_unused_auto()
    check("bulk_delete_unused", n >= 1, str(n))

    print("\n==== SUMMARY ===")
    if failures:
        print("FAILED", len(failures), failures)
        return 1
    print("ALL_PASS", "0 failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
