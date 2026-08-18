"""Phase 3.2 权限一张网：单一决策器。

优先级（定死）：
  secret_floor deny > user deny > skill contract deny > path deny
  > steward (workforce) > user allow > profile gate > capability > default

每次决策输出可解释记录：
  {tool, args_digest, verdict, matched_rule, layer, reason}

kernel.mediate / tool_hooks 均应经本模块或写入同等字段的 policy.decision。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)
_last_court_policy_digest: str | None = None

Verdict = Literal["allow", "deny", "ask"]
Layer = Literal[
    "disabled",
    "secret_floor",
    "user_deny",
    "skill",
    "path",
    "steward",
    "user_allow",
    "profile",
    "capability",
    "session_grant",
    "default",
]


@dataclass
class CourtDecision:
    tool: str
    args_digest: str
    verdict: Verdict
    matched_rule: str
    layer: Layer
    reason: str = ""
    capability_checked: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_audit(self) -> dict[str, Any]:
        d = {
            "tool": self.tool,
            "args_digest": self.args_digest,
            "verdict": self.verdict,
            "matched_rule": self.matched_rule,
            "layer": self.layer,
            "reason": self.reason,
            "capability_checked": self.capability_checked,
        }
        if self.extra:
            d["extra"] = self.extra
        return d

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def args_digest(tool: str, arguments: dict[str, Any] | None) -> str:
    """稳定摘要：去掉内部 _ 键，截断长字符串。"""
    clean: dict[str, Any] = {}
    for k, v in sorted((arguments or {}).items()):
        if str(k).startswith("_"):
            continue
        if isinstance(v, str) and len(v) > 200:
            clean[k] = v[:200] + "…"
        else:
            clean[k] = v
    raw = json.dumps({"tool": tool, "args": clean}, sort_keys=True, ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _settings() -> Any:
    from backend.core.config import settings

    return settings


def decide_capability(
    *,
    process_id: str,
    action: str,
    target: str,
    proc: Any | None,
    args: dict[str, Any] | None = None,
) -> CourtDecision:
    """kernel.mediate 能力层裁决（同步、无 await）。"""
    tool = f"{action}:{target}"
    digest = args_digest(tool, args)
    if proc is None:
        return CourtDecision(
            tool=tool,
            args_digest=digest,
            verdict="deny",
            matched_rule="capability:unknown_process",
            layer="capability",
            reason=f"未知进程 {process_id}",
            capability_checked=True,
        )
    if getattr(proc, "is_terminal", False):
        return CourtDecision(
            tool=tool,
            args_digest=digest,
            verdict="deny",
            matched_rule="capability:terminal",
            layer="capability",
            reason=f"进程已终止（{getattr(proc, 'state', '?')}）",
            capability_checked=True,
        )
    token = getattr(proc, "token", None)
    if token is not None:
        if getattr(token, "is_expired", False):
            return CourtDecision(
                tool=tool,
                args_digest=digest,
                verdict="deny",
                matched_rule="capability:token_expired",
                layer="capability",
                reason="能力令牌已过期",
                capability_checked=True,
                extra={"token_id": getattr(token, "id", None)},
            )
        allows = getattr(token, "allows", None)
        if callable(allows) and not allows(target):
            return CourtDecision(
                tool=tool,
                args_digest=digest,
                verdict="deny",
                matched_rule="capability:token_scope",
                layer="capability",
                reason=f"令牌范围不含 '{target}'（action={action}）",
                capability_checked=True,
                extra={"token_id": getattr(token, "id", None)},
            )
        return CourtDecision(
            tool=tool,
            args_digest=digest,
            verdict="allow",
            matched_rule="capability:token_ok",
            layer="capability",
            reason="token mediated",
            capability_checked=True,
            extra={"token_id": getattr(token, "id", None)},
        )
    caps = getattr(proc, "capabilities", None)
    has_cap = getattr(proc, "has_capability", None)
    if caps is not None and callable(has_cap) and not has_cap(target):
        return CourtDecision(
            tool=tool,
            args_digest=digest,
            verdict="deny",
            matched_rule="capability:set_miss",
            layer="capability",
            reason=f"能力集不含 '{target}'（action={action}）",
            capability_checked=True,
        )
    return CourtDecision(
        tool=tool,
        args_digest=digest,
        verdict="allow",
        matched_rule="capability:compat" if caps is None else "capability:set_ok",
        layer="capability",
        reason="mediated",
        capability_checked=caps is not None,
    )


def _court_from_rust_dict(d: dict[str, Any]) -> CourtDecision:
    return CourtDecision(
        tool=str(d.get("tool") or ""),
        args_digest=str(d.get("args_digest") or ""),
        verdict=str(d.get("verdict") or "deny"),  # type: ignore[arg-type]
        matched_rule=str(d.get("matched_rule") or ""),
        layer=str(d.get("layer") or "default"),  # type: ignore[arg-type]
        reason=str(d.get("reason") or ""),
        capability_checked=bool(d.get("capability_checked")),
        extra=dict(d.get("extra") or {}) if isinstance(d.get("extra"), dict) else {},
    )


def _try_rust_decide_tool(
    name: str,
    args: dict[str, Any],
    *,
    skill_contract: Any | None = None,
) -> CourtDecision | None:
    """P0-D：优先走 Rust court（secret/path/steward/capability/profile）。"""
    try:
        from backend.kernel import get_kernel
        from backend.kernel.tool_gate import sanitize_args_for_kernel

        k = get_kernel()
        if not hasattr(k, "_call"):
            return None
        # push workspace + light policy once best-effort
        try:
            from pathlib import Path

            from backend.tools.permissions import resolve_agent_workspace_root

            root = str(Path(resolve_agent_workspace_root()))
        except Exception:
            root = ""
        skill_tools = None
        skill_deny = None
        if skill_contract is not None:
            allowed = getattr(skill_contract, "tools", None) or getattr(
                skill_contract, "allowed_tools", None
            )
            if allowed is not None:
                skill_tools = [str(x) for x in allowed]
            perms = getattr(skill_contract, "permissions", None) or {}
            if isinstance(perms, dict) and perms.get("deny"):
                skill_deny = [str(x) for x in perms["deny"]]
        pid = str(args.get("_kernel_process_id") or args.get("_process_id") or "") or None
        # Never ship live objects (ConnectionManager / recorder) over JSON-RPC.
        safe_args = sanitize_args_for_kernel(args)
        # Absolute-in-workspace / ~/.tevarn → relative (Rust path:workspace deny fix)
        try:
            from backend.tools.permissions import normalize_tool_path_args

            safe_args = normalize_tool_path_args(safe_args)
        except Exception:
            pass
        # Keep identity caps for steward layer (strings only)
        if isinstance(args.get("_identity_capabilities"), (list, tuple)):
            safe_args["_identity_capabilities"] = [
                str(x) for x in args["_identity_capabilities"]
            ]
        if args.get("_identity_id"):
            safe_args["_identity_id"] = str(args["_identity_id"])
        if args.get("_workforce") is True:
            safe_args["_workforce"] = True
        if args.get("_session_id"):
            safe_args["_session_id"] = str(args["_session_id"])
        # sync identity caps for steward if present
        payload = {
            "name": name,
            "args": safe_args,
            "process_id": pid,
            "skill_tools": skill_tools,
            "skill_deny": skill_deny,
            "emit": True,
        }
        policy_payload: dict[str, Any] = {
            "permission_enabled": bool(
                getattr(_settings(), "agent_permission_enabled", True)
            ),
            "relax_secrets": bool(
                getattr(_settings(), "agent_permission_relax_secrets", False)
            ),
        }
        if root:
            policy_payload["workspace_root"] = root
        # push user deny/allow + active profile so Rust is not incomplete
        try:
            from backend.agent.permission_overlay import load_user_rules_payload
            from backend.agent.working_mode import effective_permission_profile

            payload_rules = load_user_rules_payload()
            policy_payload["user_deny"] = [
                str(p) for p in (payload_rules.get("deny") or [])
            ]
            policy_payload["user_allow"] = [
                str(p) for p in (payload_rules.get("allow") or [])
            ]
            policy_payload["profile"] = str(
                effective_permission_profile() or "build"
            )
            # sandbox profile may force plan/readonly
            try:
                from backend.computer.profiles import resolve_profile

                sprof = resolve_profile()
                if sprof.force_working_mode == "readonly":
                    policy_payload["profile"] = "plan"
            except Exception:
                pass
        except Exception:
            pass
        try:
            from backend.tools.permissions import get_run_extra_roots, host_data_roots

            roots: list[str] = []
            seen: set[str] = set()
            for r in [*get_run_extra_roots(), *host_data_roots()]:
                sroot = str(r or "").strip()
                if not sroot or sroot in seen:
                    continue
                seen.add(sroot)
                roots.append(sroot)
            policy_payload["extra_roots"] = roots
        except Exception:
            pass
        policy_payload["allow_mcp_prefix"] = True
        # 审计 P1-K2：set_court_policy 失败不得静默继续用陈旧/默认策略裁决
        global _last_court_policy_digest
        try:
            blob = json.dumps(policy_payload, sort_keys=True, default=str)
            digest_p = hashlib.sha256(blob.encode("utf-8")).hexdigest()
            if digest_p != _last_court_policy_digest:
                k._call("set_court_policy", policy_payload)
                _last_court_policy_digest = digest_p
        except Exception as policy_err:
            logger.warning(
                "set_court_policy failed tool=%s: %s: %s — fail-closed deny",
                name,
                type(policy_err).__name__,
                policy_err,
            )
            return CourtDecision(
                tool=name,
                args_digest=args_digest(name, args if isinstance(args, dict) else {}),
                verdict="deny",
                matched_rule="court:policy_sync_failed",
                layer="secret_floor",
                reason=f"set_court_policy failed: {policy_err}",
            )
        r = k._call("decide_tool", payload)
        if isinstance(r, dict) and r.get("verdict"):
            return _court_from_rust_dict(r)
        logger.warning(
            "rust decide_tool empty/invalid response tool=%s r=%s",
            name,
            type(r).__name__,
        )
    except Exception as e:
        # Visible: fail-closed path depends on this; debug hides root cause
        logger.warning(
            "rust decide_tool failed tool=%s: %s: %s",
            name,
            type(e).__name__,
            e,
        )
    return None


async def decide_tool(
    name: str,
    arguments: dict[str, Any] | None,
    *,
    skill_contract: Any | None = None,
) -> CourtDecision:
    """工具调用完整裁决。P0-D：优先 Rust court，再 Python 完整 profile gate。"""
    args = dict(arguments or {})
    digest = args_digest(name, args)
    s = _settings()

    if not bool(getattr(s, "agent_permission_enabled", True)):
        logger.info(
            "court short-circuit allow tool=%s (agent_permission_enabled=false)",
            name,
        )
        return CourtDecision(
            tool=name,
            args_digest=digest,
            verdict="allow",
            matched_rule="disabled",
            layer="disabled",
            reason="agent_permission_enabled=false",
        )

    # T1：Rust court 为权威——有结果直接返回。
    # extra_roots / mcp_* / session grants 由 host CourtPolicy + SessionGrantStore 裁决。
    # host 可用且 agent_court_rust_required 时：Rust 失败 = deny（禁止静默放宽）。
    rust_dec = _try_rust_decide_tool(name, args, skill_contract=skill_contract)
    if rust_dec is not None:
        return rust_dec

    rust_required = bool(getattr(s, "agent_court_rust_required", True))
    rust_backend = str(getattr(s, "agent_kernel_backend", "rust") or "rust").lower()
    # 仅当 host **已在监听** 却未给出合法裁决时 fail-closed（避免单测无 host 全死）
    if (
        rust_required
        and rust_backend == "rust"
        and bool(getattr(s, "agent_kernel_enabled", True))
    ):
        host_up = False
        try:
            from backend.kernel_rust.client import is_rust_host_available

            host_up = bool(is_rust_host_available())
        except Exception:
            host_up = False
        if host_up:
            return CourtDecision(
                tool=name,
                args_digest=digest,
                verdict="deny",
                matched_rule="court:rust_unavailable",
                layer="secret_floor",
                reason=(
                    "Rust court 未返回裁决（host 在线但 decide_tool 失败）；"
                    "agent_court_rust_required=true，已 fail-closed。"
                    "可设 agent_court_rust_required=false 回退 Python。"
                ),
            )

    # ── fallback：Python 完整 path ──
    # H3: production forbids Python court when rust is the configured backend
    # unless DEV_UNSAFE / agent_court_rust_required=false.
    try:
        from backend.kernel.production_guard import is_dev_unsafe, is_production_guard

        if (
            rust_required
            and rust_backend == "rust"
            and is_production_guard()
            and not is_dev_unsafe()
        ):
            return CourtDecision(
                tool=name,
                args_digest=digest,
                verdict="deny",
                matched_rule="court:python_tail_locked",
                layer="secret_floor",
                reason=(
                    "Python court fallback locked in production (H3). "
                    "Host must serve decide_tool. "
                    "Rebuild host or set TEVARN_DEV_UNSAFE=1 / "
                    "agent_court_rust_required=false for local only."
                ),
            )
    except Exception:
        pass

    # 1) secret floor + user deny (DEV / non-rust path only)
    denied = _check_deny_layers(name, args)
    if denied is not None:
        return denied

    # 1b) mounted MCP runtime tools (parity with Rust CourtPolicy.allow_mcp_prefix)
    if str(name).startswith("mcp_"):
        return CourtDecision(
            tool=name,
            args_digest=digest,
            verdict="allow",
            matched_rule="mcp:mounted_allow",
            layer="capability",
            reason="MCP runtime tool allowlisted (mounted server)",
            capability_checked=True,
        )

    # 2) skill contract tools 白名单
    if skill_contract is not None:
        try:
            allowed_tools = getattr(skill_contract, "tools", None) or getattr(
                skill_contract, "allowed_tools", None
            )
            if allowed_tools is not None:
                names = {str(x) for x in allowed_tools}
                if names and name not in names:
                    return CourtDecision(
                        tool=name,
                        args_digest=digest,
                        verdict="deny",
                        matched_rule=f"skill:tools!{name}",
                        layer="skill",
                        reason=f"skill contract 不允许工具 {name}",
                    )
            perms = getattr(skill_contract, "permissions", None) or {}
            if isinstance(perms, dict) and perms.get("deny"):
                deny_list = {str(x) for x in perms["deny"]}
                if name in deny_list:
                    return CourtDecision(
                        tool=name,
                        args_digest=digest,
                        verdict="deny",
                        matched_rule=f"skill:deny:{name}",
                        layer="skill",
                        reason="skill contract deny",
                    )
        except Exception as e:
            logger.debug("skill contract check: %s", e)

    # 3) path whitelist (ToolPermissionManager)
    path_dec = _check_path_permission(name, args)
    if path_dec is not None and path_dec.verdict == "deny":
        return path_dec

    # 4) workforce steward
    try:
        from backend.agent.steward_permission import (
            is_human_strategy_surface,
            is_workforce_context,
            load_identity_capabilities,
            steward_decide_tool,
        )

        if is_workforce_context(args) and not is_human_strategy_surface(name):
            iid = str(args.get("_identity_id") or "").strip() or None
            caps = await load_identity_capabilities(iid)
            if not isinstance(caps, list):
                raw = args.get("_identity_capabilities")
                caps = list(raw) if isinstance(raw, list) else None
            if caps is not None:
                args["_identity_capabilities"] = list(caps)
            decision, why = await steward_decide_tool(
                name, args, identity_capabilities=caps
            )
            if decision == "allow":
                return CourtDecision(
                    tool=name,
                    args_digest=digest,
                    verdict="allow",
                    matched_rule="steward:allow",
                    layer="steward",
                    reason=str(why),
                    extra={"identity_id": iid},
                )
            return CourtDecision(
                tool=name,
                args_digest=digest,
                verdict="deny",
                matched_rule="steward:deny",
                layer="steward",
                reason=str(why),
                extra={"identity_id": iid},
            )
    except Exception as e:
        if str(args.get("_agent_key") or "").startswith("wf:") or args.get("_workforce"):
            return CourtDecision(
                tool=name,
                args_digest=digest,
                verdict="deny",
                matched_rule="steward:error",
                layer="steward",
                reason=f"编制权限裁决失败: {e}",
            )

    # 5) session grant 短路（在 ask 之前先查；也在 profile 之后再查一次）
    # 6) profile + user allow via PermissionGate（last-match 已合并 overlay）
    from backend.agent.permission_overlay import build_effective_rules
    from backend.agent.permissions_rules import PermissionGate
    from backend.agent.working_mode import (
        effective_permission_profile,
    )

    profile = effective_permission_profile()
    try:
        from backend.computer.profiles import resolve_profile

        sprof = resolve_profile()
        if sprof.force_working_mode == "readonly":
            profile = "plan"
    except Exception:
        pass

    mode = "build"
    chat_mode = str(args.get("_chat_mode") or getattr(s, "_active_chat_mode", "") or "")
    session_id = str(args.get("_session_id") or "") or None
    job_id = str(args.get("_inbox_item_id") or args.get("_job_id") or "") or None
    try:
        from backend.agent.plan_session import requires_plan_approval

        if requires_plan_approval(session_id=session_id, job_id=job_id, chat_mode=chat_mode):
            mode = "plan"
            profile = "plan"
    except Exception:
        pass
    if profile.lower() == "plan" or chat_mode.lower() in ("plan", "ask", "explore"):
        mode = "plan"
        if profile.lower() != "plan":
            profile = "plan"

    try:
        from pathlib import Path

        from backend.tools.permissions import resolve_agent_workspace_root

        root = Path(resolve_agent_workspace_root())
    except Exception:
        from pathlib import Path

        root = Path.cwd()

    rules = build_effective_rules(profile)
    gate = PermissionGate(profile=profile, mode=mode, project_root=root, rules=rules)
    decision = gate.check(name, args)

    # user allow 显式命中（overlay 中 allow 规则）— 用 matched 信息推断 layer
    layer: Layer = "profile"
    matched = f"profile:{profile}:{decision}"
    if decision == "allow":
        # 若 user allow 列表包含该工具名
        try:
            from backend.agent.permission_overlay import load_user_rules_payload

            payload = load_user_rules_payload()
            allow_pats = [str(p) for p in (payload.get("allow") or [])]
            if any(p == name or name in p for p in allow_pats):
                layer = "user_allow"
                matched = "user_allow"
        except Exception:
            pass

    if decision == "allow" and _tool_self_declares_confirmation(name):
        decision = "ask"
        layer = "profile"
        matched = "tool:requires_confirmation"

    if decision == "ask":
        try:
            from backend.agent.grant_store import has_session_grant

            sid = str(args.get("_session_id") or "")
            if has_session_grant(sid, name, args):
                return CourtDecision(
                    tool=name,
                    args_digest=digest,
                    verdict="allow",
                    matched_rule="session_grant",
                    layer="session_grant",
                    reason="本会话已授权",
                    extra={"_confirm_ok": True},
                )
        except Exception:
            pass

    if decision not in ("allow", "deny", "ask"):
        decision = "ask"
        layer = "default"
        matched = "default:ask"

    return CourtDecision(
        tool=name,
        args_digest=digest,
        verdict=decision,  # type: ignore[arg-type]
        matched_rule=matched,
        layer=layer,
        reason=f"gate profile={profile} mode={mode}",
        extra={"profile": profile, "mode": mode},
    )


def _check_deny_layers(name: str, args: dict[str, Any]) -> CourtDecision | None:
    """secret floor + user deny：用 PermissionGate 对 deny-only 规则集求值。"""
    digest = args_digest(name, args)
    try:
        from pathlib import Path

        from backend.agent.dangerous_paths import secret_deny_rules
        from backend.agent.permission_overlay import load_user_rules_payload
        from backend.agent.permission_rules_dsl import rules_from_payload
        from backend.agent.permissions_rules import PermissionGate
        from backend.core.config import settings

        try:
            from backend.tools.permissions import resolve_agent_workspace_root

            root = Path(resolve_agent_workspace_root())
        except Exception:
            root = Path.cwd()

        if not bool(getattr(settings, "agent_permission_relax_secrets", False)):
            secrets = [r for r in secret_deny_rules() if r.decision == "deny"]
            if secrets:
                g = PermissionGate(profile="build", mode="build", project_root=root, rules=secrets)
                # 先看 secret allow 是否覆盖（.env.example）— secret_deny_rules 含 allow
                all_secret = secret_deny_rules()
                g2 = PermissionGate(profile="build", mode="build", project_root=root, rules=all_secret)
                d = g2.check(name, args)
                if d == "deny":
                    return CourtDecision(
                        tool=name,
                        args_digest=digest,
                        verdict="deny",
                        matched_rule="secret_floor",
                        layer="secret_floor",
                        reason="secret floor deny",
                    )

        payload = load_user_rules_payload()
        deny_pats = list(payload.get("deny") or [])
        if deny_pats:
            user_deny = rules_from_payload({"deny": deny_pats, "allow": [], "ask": []})
            if user_deny:
                g = PermissionGate(
                    profile="build", mode="build", project_root=root, rules=user_deny
                )
                if g.check(name, args) == "deny":
                    return CourtDecision(
                        tool=name,
                        args_digest=digest,
                        verdict="deny",
                        matched_rule=f"user_deny:{deny_pats[0]}",
                        layer="user_deny",
                        reason="user deny rule",
                    )
    except Exception as e:
        logger.debug("deny layers: %s", e)
    return None


# H2-B1: common tool arg keys that carry filesystem paths
_PATH_ARG_KEYS = (
    "path",
    "file",
    "filepath",
    "file_path",
    "filename",
    "target",
    "src",
    "dst",
    "source",
    "destination",
    "directory",
    "dir",
    "cwd",
    "uri",
    "url",  # file:// only checked below
    "working_directory",
    "workdir",
)


def _extract_path_candidates(args: dict[str, Any]) -> list[str]:
    out: list[str] = []
    try:
        from backend.tools.permissions import rewrite_host_path_into_workspace
    except Exception:
        rewrite_host_path_into_workspace = None  # type: ignore[assignment]
    for k in _PATH_ARG_KEYS:
        v = args.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if not s:
            continue
        if k in ("url", "uri") and not s.lower().startswith("file:"):
            continue
        if s.lower().startswith("file://"):
            s = s[7:]
            if s.startswith("/") and len(s) > 2 and s[2] == ":":
                # file:///C:/...
                s = s.lstrip("/")
        if rewrite_host_path_into_workspace is not None:
            try:
                s = rewrite_host_path_into_workspace(s)
            except Exception:
                pass
        out.append(s)
    return out


def _check_path_permission(name: str, args: dict[str, Any]) -> CourtDecision | None:
    digest = args_digest(name, args)
    candidates = _extract_path_candidates(args)
    if not candidates:
        return None
    try:
        from backend.tools.permissions import ToolPermissionManager

        mgr = ToolPermissionManager()
        for path in candidates:
            if not mgr.is_path_allowed(path):
                return CourtDecision(
                    tool=name,
                    args_digest=digest,
                    verdict="deny",
                    matched_rule="path:whitelist",
                    layer="path",
                    reason=f"path not allowed: {path[:120]}",
                )
    except Exception as e:
        logger.debug("path permission: %s", e)
    return None


def _tool_self_declares_confirmation(name: str) -> bool:
    """工具自声明 requires_confirmation（与 tool_hooks 对齐）。"""
    try:
        from backend.agent.tool_hooks import _tool_self_declares_confirmation as _inner

        return bool(_inner(name))
    except Exception:
        return False


__all__ = [
    "CourtDecision",
    "args_digest",
    "decide_capability",
    "decide_tool",
]
