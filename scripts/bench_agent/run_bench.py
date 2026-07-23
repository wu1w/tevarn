#!/usr/bin/env python3
"""Takton L0 agent bench — dual model (local-llm / kimi coding).

Usage:
  set -a; source /opt/hermes-workspace/.secrets/bench_llm.env; set +a
  cd /opt/hermes-workspace/takton
  .venv311/bin/python scripts/bench_agent/run_bench.py --models local-llm,kimi --limit 10

Does not load secrets into git. Credentials via env only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@dataclass
class ModelCfg:
    name: str
    base_url: str
    model: str
    api_key: str
    temperature: float = 0.2


@dataclass
class CaseResult:
    case_id: str
    model: str
    pass_: bool
    wall_s: float
    rounds: int
    tools: list[str] = field(default_factory=list)
    prompt_tokens_last: int | None = None
    final: str = ""
    reasons: list[str] = field(default_factory=list)
    error: str = ""


def load_models(which: list[str]) -> list[ModelCfg]:
    out: list[ModelCfg] = []
    for w in which:
        w = w.strip().lower()
        if w == "local-llm":
            out.append(
                ModelCfg(
                    name="local-llm",
                    base_url=os.environ.get("BENCH_MIMO_BASE_URL", "https://opencode.ai/zen/go").rstrip("/"),
                    model=os.environ.get("BENCH_MIMO_MODEL", "local-llm"),
                    api_key=os.environ.get("BENCH_MIMO_API_KEY")
                    or os.environ.get("OPENCODE_API_KEY")
                    or "",
                    temperature=0.2,
                )
            )
        elif w in ("kimi", "kimi-k3", "k3"):
            out.append(
                ModelCfg(
                    name="kimi",
                    base_url=os.environ.get(
                        "BENCH_KIMI_BASE_URL", "https://api.kimi.com/coding/v1"
                    ).rstrip("/"),
                    # Coding API catalog id (user key); label as K3 track in reports
                    model=os.environ.get("BENCH_KIMI_MODEL", "kimi-for-coding"),
                    api_key=os.environ.get("BENCH_KIMI_API_KEY")
                    or os.environ.get("KIMI_API_KEY")
                    or "",
                    temperature=1.0,  # coding endpoint only allows 1
                )
            )
        else:
            raise SystemExit(f"unknown model alias: {w}")
    for m in out:
        if not m.api_key:
            raise SystemExit(f"missing api key for {m.name}")
    return out


async def chat(
    cfg: ModelCfg,
    messages: list[dict],
    tools: list[dict] | None,
    *,
    max_tokens: int = 900,
) -> tuple[dict | None, dict | None, float, str]:
    base = cfg.base_url.rstrip("/")
    if base.endswith("/v1"):
        url = f"{base}/chat/completions"
    else:
        url = f"{base}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
        "User-Agent": "takton-bench/0.1",
    }
    body: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(url, headers=headers, json=body)
        dt = time.time() - t0
        if r.status_code != 200:
            return None, None, dt, f"HTTP {r.status_code}: {r.text[:300]}"
        data = r.json()
        msg = data["choices"][0]["message"]
        return msg, data.get("usage"), dt, ""
    except Exception as e:
        return None, None, time.time() - t0, str(e)


def eval_case(case: dict, tools_used: list[str], final: str) -> tuple[bool, list[str]]:
    exp = case.get("expect") or {}
    reasons: list[str] = []
    ok = True
    final_l = final or ""

    want_any = exp.get("tools_any") or []
    if want_any:
        if not any(t in tools_used for t in want_any):
            ok = False
            reasons.append(f"missing tools_any={want_any} got={tools_used}")

    if exp.get("tools_none_required"):
        pass

    for s in exp.get("final_contains_all") or []:
        if s not in final_l:
            ok = False
            reasons.append(f"missing all-token: {s}")

    any_toks = exp.get("final_contains_any") or []
    if any_toks and not any(s in final_l for s in any_toks):
        ok = False
        reasons.append(f"missing any-token in {any_toks}")

    min_c = int(exp.get("final_min_chars") or 0)
    if min_c and len(final_l.strip()) < min_c:
        ok = False
        reasons.append(f"final too short <{min_c}")

    if not final_l.strip() and not tools_used:
        ok = False
        reasons.append("empty final and no tools")

    return ok, reasons


async def run_one(
    cfg: ModelCfg,
    case: dict,
    tools_schema: list[dict],
    *,
    max_rounds: int = 6,
) -> CaseResult:
    from backend.tools.registry import ToolRegistry

    sys_msg = (
        "You are Takton coding agent under benchmark. "
        "Workspace is the project root. Use relative paths like backend/.... "
        "Use tools for file/shell facts. After tools, answer concisely in Chinese."
    )
    user = (case.get("user") or "").strip()
    messages: list[dict] = [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": user},
    ]
    tools_used: list[str] = []
    final = ""
    wall = 0.0
    last_usage = None
    err = ""
    rounds = 0

    for _ in range(max_rounds):
        rounds += 1
        msg, usage, dt, err = await chat(cfg, messages, tools_schema)
        wall += dt
        last_usage = usage
        if not msg:
            break
        tcs = msg.get("tool_calls") or []
        content = msg.get("content") or ""
        if not tcs:
            final = content
            break
        messages.append(
            {
                "role": "assistant",
                "content": content or None,
                "tool_calls": tcs,
            }
        )
        for tc in tcs:
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            tools_used.append(name)
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            try:
                result = str(await ToolRegistry.execute(name, args))
            except Exception as e:
                result = f"[Error] {e}"
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "name": name,
                    "content": result[:15000],
                }
            )

    passed, reasons = eval_case(case, tools_used, final)
    if err:
        reasons.append(err)
        passed = False
    return CaseResult(
        case_id=str(case.get("id")),
        model=f"{cfg.name}:{cfg.model}",
        pass_=passed,
        wall_s=round(wall, 2),
        rounds=rounds,
        tools=tools_used,
        prompt_tokens_last=(last_usage or {}).get("prompt_tokens"),
        final=(final or "")[:500],
        reasons=reasons,
        error=err,
    )


async def main_async(args: argparse.Namespace) -> int:
    from backend.tools.loader import load_all_tools
    from backend.tools.registry import ToolRegistry
    from backend.agent.tool_policy import resolve_enabled_tool_names
    from backend.tools.permissions import ToolPermissionManager

    cases_path = Path(args.cases)
    if not cases_path.is_absolute():
        cases_path = ROOT / cases_path
    doc = yaml.safe_load(cases_path.read_text(encoding="utf-8"))
    cases = list(doc.get("cases") or [])
    if args.limit:
        cases = cases[: int(args.limit)]

    models = load_models(args.models.split(","))
    await load_all_tools()
    mgr = ToolPermissionManager()
    print("workspace_root", mgr.workspace_root)

    names, plan = resolve_enabled_tool_names(
        profile="dynamic",
        user_input="read backend file coding",
    )
    tools_schema = ToolRegistry.get_tools_schema(names)
    print("tools", len(tools_schema), "scene", plan.summary())

    results: list[CaseResult] = []
    for cfg in models:
        print(f"\n===== model {cfg.name} {cfg.model} =====")
        for case in cases:
            print(f"  case {case.get('id')} ...", flush=True)
            r = await run_one(cfg, case, tools_schema, max_rounds=args.max_rounds)
            results.append(r)
            flag = "PASS" if r.pass_ else "FAIL"
            print(
                f"    {flag} {r.wall_s}s rounds={r.rounds} tools={r.tools} "
                f"tok={r.prompt_tokens_last} {r.reasons[:2]}"
            )

    # summary
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jsonl = out_dir / f"bench_{ts}.jsonl"
    with jsonl.open("w", encoding="utf-8") as f:
        for r in results:
            row = asdict(r)
            row["pass"] = row.pop("pass_")
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    lines = [
        f"# Bench report {ts}",
        "",
        f"- workspace: `{mgr.workspace_root}`",
        f"- tools_n: {len(tools_schema)}",
        f"- cases: {len(cases)}",
        f"- models: {', '.join(m.name+':'+m.model for m in models)}",
        "",
        "| model | pass | total | pass_rate | avg_wall_s | avg_prompt_tok |",
        "|-------|------|-------|-----------|------------|----------------|",
    ]
    by_model: dict[str, list[CaseResult]] = {}
    for r in results:
        by_model.setdefault(r.model, []).append(r)
    for mid, rs in by_model.items():
        p = sum(1 for x in rs if x.pass_)
        n = len(rs)
        avg_w = sum(x.wall_s for x in rs) / max(1, n)
        toks = [x.prompt_tokens_last for x in rs if x.prompt_tokens_last is not None]
        avg_t = sum(toks) / len(toks) if toks else 0
        lines.append(
            f"| {mid} | {p} | {n} | {p/n:.0%} | {avg_w:.1f} | {avg_t:.0f} |"
        )
    lines.append("")
    lines.append("## Failures")
    for r in results:
        if not r.pass_:
            lines.append(
                f"- **{r.model} / {r.case_id}**: {r.reasons} final={r.final[:120]!r}"
            )
    md = out_dir / f"bench_{ts}.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # also write latest pointer
    (out_dir / "LATEST.md").write_text(md.read_text(encoding="utf-8"), encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\nWrote {jsonl}")
    print(f"Wrote {md}")
    return 0 if all(r.pass_ for r in results) else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="local-llm,kimi", help="comma: local-llm,kimi")
    ap.add_argument("--cases", default="scripts/bench_agent/cases_v1.yaml")
    ap.add_argument("--out", default="docs/bench")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-rounds", type=int, default=6)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
