"""Nested subagents — Claude Explore / general-purpose style (no recursive spawn)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from takton_code.agent.prompt import build_system_prompt
from takton_code.agent.tools import ToolRuntime
from takton_code.diff.engine import DiffEngine
from takton_code.llm.provider import LLMProvider, collect_stream


async def run_subagent(
    *,
    llm: LLMProvider,
    project_root: Any,
    project_block: str,
    bridge: Any | None,
    agent: str,
    prompt: str,
    max_iterations: int = 12,
    test_command: str | None = None,
    on_event: Any | None = None,
) -> str:
    """
    Run a short nested agent loop.
    agent=explore → read-only tools, no spawn
    agent=general → write tools allowed, no spawn_subagent
    """
    agent = (agent or "explore").lower().strip()
    if agent in ("explore", "plan", "ask"):
        mode = "explore" if agent == "explore" else agent
        readonly = True
    else:
        mode = "build"
        readonly = False

    def emit(typ: str, **payload: Any) -> None:
        if on_event:
            on_event({"type": typ, "subagent": agent, **payload})

    diff = DiffEngine(project_root)
    tools = ToolRuntime(
        project_root,
        diff,
        mode=mode if mode != "explore" else "explore",
        test_command=test_command,
        allow_git_commit=False,
        allow_git_push=False,
        bridge=bridge,
        enable_subagent=False,  # hard wall — no nested spawn
    )
    system = build_system_prompt(
        mode=mode,
        project_block=project_block,
        extra_skills=(
            "You are a SUBAGENT. Do not claim to be the parent. "
            "Return a concise final answer for the parent agent. "
            "Do not call spawn_subagent."
        ),
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    emit("subagent_start", agent=agent, prompt=prompt[:200])
    final = ""
    for i in range(max(1, max_iterations)):
        emit("subagent_step", n=i + 1)
        tool_schemas = tools.openai_tools(readonly_only=readonly)
        # strip spawn if somehow present
        tool_schemas = [t for t in tool_schemas if (t.get("function") or {}).get("name") != "spawn_subagent"]

        resp = await collect_stream(
            llm,
            messages,
            tools=tool_schemas or None,
            on_delta=lambda ev: emit("subagent_delta", **{k: v for k, v in ev.items() if k != "type"}, delta_type=ev.get("type")),
        )
        if resp.tool_calls:
            a_msg: dict[str, Any] = {
                "role": "assistant",
                "content": resp.content if resp.content else None,
                "tool_calls": resp.tool_calls,
            }
            messages.append(a_msg)
            for tc in resp.tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                tc_id = tc.get("id") or f"sub_{uuid.uuid4().hex[:8]}"
                if name == "spawn_subagent":
                    result = "ERROR: nested spawn_subagent is forbidden"
                else:
                    result = await tools.execute(name, raw_args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "name": name,
                        "content": result,
                    }
                )
                emit("subagent_tool", name=name, preview=str(result)[:200])
            continue
        final = (resp.content or "").strip()
        if final:
            messages.append({"role": "assistant", "content": final})
            break
        if resp.reasoning_content:
            messages.append(
                {
                    "role": "user",
                    "content": "Provide the final answer for the parent (no tools needed if done).",
                }
            )
            continue
        break

    emit("subagent_end", agent=agent, chars=len(final))
    if not final:
        final = "(subagent produced no text)"
    # include compact change summary if general wrote files
    if not readonly and diff.changes:
        final += "\n\n[subagent changes]\n" + "\n".join(c.summary() for c in diff.changes[-20:])
    return final


def parse_spawn_args(args: dict[str, Any] | str) -> tuple[str, str, int]:
    if isinstance(args, str):
        try:
            args = json.loads(args) if args else {}
        except json.JSONDecodeError:
            args = {"prompt": args}
    agent = str(args.get("agent") or args.get("name") or "explore")
    prompt = str(args.get("prompt") or args.get("task") or "").strip()
    max_iter = int(args.get("max_iterations") or args.get("max_iter") or 12)
    return agent, prompt, max_iter
