"""System 分层可视化：把 prompt 拆成可审计的层。"""

from __future__ import annotations

from typing import Any


def _est_tokens(text: str) -> int:
    # 粗估：中英混合约 2.2 字符/token
    if not text:
        return 0
    return max(1, int(len(text) / 2.2))


def _layer(
    *,
    id: str,
    label: str,
    source: str,
    content: str = "",
    items: list[dict[str, Any]] | None = None,
    mutable: bool = False,
) -> dict[str, Any]:
    content = content or ""
    return {
        "id": id,
        "label": label,
        "source": source,
        "mutable": mutable,
        "chars": len(content),
        "tokens_est": _est_tokens(content),
        "content": content,
        "items": items or [],
    }


def build_system_layers_report(
    *,
    parts: dict[str, str],
    identity: str | None = None,
    user_system_prompt: str | None = None,
    context_files: str | None = None,
    package_snippets: list[dict[str, str]] | None = None,
    platform: str | None = None,
    mode: str | None = None,
    memory_block: str | None = None,
    dynamic_injections: list[dict[str, Any]] | None = None,
    model: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """构造前端可视化用的分层报告。"""
    from backend.agent.system_prompt import (
        DEFAULT_IDENTITY,
        MODE_PROMPTS,
        PLATFORM_HINTS,
        merge_prompt_parts,
    )

    layers: list[dict[str, Any]] = []

    # 1) core / stable
    stable = (parts or {}).get("stable") or ""
    layers.append(
        _layer(
            id="core",
            label="Stable 核心",
            source="builtin",
            content=stable,
            mutable=False,
            items=[
                {"key": "identity", "label": "身份", "present": bool(identity or DEFAULT_IDENTITY)},
                {"key": "tool_guidance", "label": "工具/任务准则", "present": "Tool-use" in stable or "工具" in stable},
                {"key": "thinking", "label": "思考/客观性", "present": True},
            ],
        )
    )

    # 2) profile
    profile = (user_system_prompt or "").strip()
    layers.append(
        _layer(
            id="profile",
            label="人格 / 用户配置",
            source="ctx_item|session_config",
            content=profile,
            mutable=True,
            items=[{"key": "user_system_prompt", "present": bool(profile)}],
        )
    )

    # 3) context files
    files = (context_files or "").strip()
    layers.append(
        _layer(
            id="context_files",
            label="上下文文件 (AGENTS.md 等)",
            source="workspace|ctx_item",
            content=files,
            mutable=True,
        )
    )

    # 4) packages / plugins
    pkg_items = []
    pkg_texts = []
    for sn in package_snippets or []:
        name = sn.get("name") or "package"
        icon = sn.get("icon") or "📦"
        body = (sn.get("content") or "").strip()
        pkg_items.append({"name": name, "icon": icon, "chars": len(body)})
        if body:
            pkg_texts.append(f"### {icon} {name}\n{body}")
    pkg_content = "\n\n".join(pkg_texts)
    layers.append(
        _layer(
            id="packages",
            label="会话挂载包 (plugins)",
            source="session.config.attached_packages",
            content=pkg_content,
            mutable=True,
            items=pkg_items,
        )
    )

    # 5) mode / platform
    mode_text = ""
    if mode and mode in MODE_PROMPTS:
        mode_text = MODE_PROMPTS[mode]
    plat_text = ""
    if platform and platform in PLATFORM_HINTS:
        plat_text = PLATFORM_HINTS[platform]
    mode_plat = "\n\n".join(x for x in [mode_text, plat_text] if x)
    layers.append(
        _layer(
            id="mode_platform",
            label="模式 / 平台提示",
            source="runtime",
            content=mode_plat,
            mutable=True,
            items=[
                {"key": "mode", "value": mode or "default"},
                {"key": "platform", "value": platform or "desktop"},
            ],
        )
    )

    # 6) dynamic (cluster/rag/goal 等 — 调用方传入摘要)
    dyn_items = list(dynamic_injections or [])
    dyn_content = "\n\n".join(
        f"[{d.get('kind', 'dynamic')}] {d.get('summary') or d.get('content') or ''}"[:2000]
        for d in dyn_items
        if d
    )
    layers.append(
        _layer(
            id="dynamic",
            label="动态注入 (RAG / 集群 / Goal…)",
            source="runtime_loop",
            content=dyn_content,
            mutable=True,
            items=dyn_items,
        )
    )

    # 7) volatile
    volatile = (parts or {}).get("volatile") or ""
    # memory is inside volatile if present; also expose separately if given
    mem = (memory_block or "").strip()
    layers.append(
        _layer(
            id="volatile",
            label="Volatile 易变层",
            source="per_turn",
            content=volatile if volatile else mem,
            mutable=True,
            items=[
                {"key": "memory", "present": bool(mem)},
                {"key": "session", "value": (session_id or "")[:8]},
                {"key": "model", "value": model or ""},
            ],
        )
    )

    # merged preview: stable+context+packages injected into context conceptually
    # Actual merge used by runtime is stable+context+volatile; packages should be part of context
    context_with_pkgs = (parts or {}).get("context") or ""
    if pkg_content and pkg_content not in context_with_pkgs:
        context_with_pkgs = "\n\n".join(
            x for x in [context_with_pkgs, "# Attached packages\n" + pkg_content] if x
        )
    merged_parts = {
        "stable": (parts or {}).get("stable") or "",
        "context": context_with_pkgs,
        "volatile": (parts or {}).get("volatile") or "",
    }
    # dynamic is shown separately (injected after base system in loop)
    merged = merge_prompt_parts(merged_parts)
    if dyn_content:
        merged = merged + "\n\n# Dynamic injections\n" + dyn_content

    totals = {
        "chars": sum(int(l.get("chars") or 0) for l in layers),
        "tokens_est": sum(int(l.get("tokens_est") or 0) for l in layers),
        "merged_chars": len(merged),
        "merged_tokens_est": _est_tokens(merged),
    }

    return {
        "layers": layers,
        "parts": merged_parts,
        "merged_preview": merged[:12000] + ("…" if len(merged) > 12000 else ""),
        "totals": totals,
        "legend": [
            {"id": "core", "desc": "不可配置的底层准则，保持短小稳定"},
            {"id": "profile", "desc": "用户人格 / SOUL / 自定义 system"},
            {"id": "packages", "desc": "会话挂载的 Takton Package 片段"},
            {"id": "dynamic", "desc": "本轮运行时注入（集群名册、RAG 等）"},
            {"id": "volatile", "desc": "每轮重建：记忆快照与元信息"},
        ],
    }
