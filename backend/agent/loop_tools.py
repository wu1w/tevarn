"""Loop tool/RAG mixin (Phase 2.4 split from loop.py)."""
from __future__ import annotations

import logging
from typing import Any

from backend.core.config import settings

logger = logging.getLogger(__name__)


class LoopToolsMixin:
    async def _ensure_live_kernel_process(self, arguments: dict[str, Any]) -> Any:
        """Ensure loop's kernel process still exists on host (post-reconnect).

        Host process table is in-memory: restart/wipe invalidates process ids.

        Critical: host *busy* (RPC timeout / connect fail) must NOT be treated as
        "process missing". Rehydrating on every timeout creates a create_process
        storm that deadlocks the host (LISTEN but no accept) — product instability.
        """
        import time as _time

        proc = getattr(self, "_kernel_process", None)
        if proc is None:
            return None
        try:
            from backend.kernel import get_kernel

            k = get_kernel()
        except Exception:
            return proc

        host_epoch = int(getattr(k, "_host_epoch", 0) or 0)
        cached_epoch = getattr(self, "_kernel_host_epoch", None)
        live = None
        probe_soft_fail = False
        try:
            live = k.get_process(str(proc.id))
            # UI path may return degraded empty dict on failure
            if isinstance(live, dict) and live.get("_degraded"):
                probe_soft_fail = True
                live = None
            elif live is not None and not getattr(live, "id", None):
                # empty shell object
                if isinstance(live, dict) and not live.get("id"):
                    live = None
        except Exception as e:
            msg = str(e).lower()
            # Host busy / flaky — keep cached process id, do not recreate
            if any(
                x in msg
                for x in (
                    "timeout",
                    "timed out",
                    "connect",
                    "10054",
                    "10053",
                    "unavailable",
                    "closed connection",
                    "refused",
                )
            ):
                probe_soft_fail = True
                logger.debug(
                    "get_process soft-fail (host busy, skip rehydrate): %s", e
                )
            else:
                logger.debug("get_process for ensure failed: %s", e)
            live = None

        if live is not None and (
            cached_epoch is None or int(cached_epoch) == host_epoch
        ):
            self._kernel_process = live
            self._kernel_host_epoch = host_epoch
            return live

        # Soft fail + same epoch: process almost certainly still on host
        if probe_soft_fail and (
            cached_epoch is None or int(cached_epoch) == host_epoch
        ):
            return proc

        # Rate-limit rehydrate (max 1 / 8s per loop) — storm protection
        now = _time.monotonic()
        last_rh = float(getattr(self, "_last_rehydrate_at", 0.0) or 0.0)
        if now - last_rh < 8.0 and cached_epoch is not None and int(cached_epoch) == host_epoch:
            logger.debug(
                "rehydrate throttled (%.1fs since last) proc=%s",
                now - last_rh,
                str(getattr(proc, "id", ""))[:12],
            )
            return proc

        # Missing on host or host epoch advanced → recreate
        try:
            self._last_rehydrate_at = now
            old = proc
            caps = list(getattr(old, "capabilities", None) or []) or None
            budget = getattr(old, "token_budget", None)
            meta = dict(getattr(old, "meta", None) or {})
            meta["rehydrated_from"] = str(getattr(old, "id", "") or "")
            meta["rehydrate_reason"] = (
                "host_epoch_bump" if live is None and cached_epoch != host_epoch else "missing"
            )
            _sid = (
                meta.get("session_id")
                or arguments.get("_session_id")
                or getattr(self, "_session_id", None)
            )
            # Prefer coding_profile_spawn path when engineering profile was on
            profile = str(meta.get("coding_profile") or "")
            new_p = None
            if profile and hasattr(k, "_call"):
                try:
                    r = k._call(
                        "coding_profile_spawn",
                        {
                            "identity": str(
                                getattr(self, "_agent_key", None) or "main"
                            ),
                            "profile": profile,
                            "session_id": str(_sid) if _sid else None,
                        },
                    )
                    if isinstance(r, dict) and r.get("process"):
                        from backend.kernel_rust.client import RustKernelProcess

                        new_p = RustKernelProcess(r["process"], k)
                except Exception as ce:
                    logger.debug("coding_profile_spawn rehydrate skip: %s", ce)
            if new_p is None:
                intent = None
                if caps:
                    intent = {
                        "goal": "rehydrate after host reconnect",
                        "capabilities": caps,
                        "constraints": {"allow_risky": True},
                    }
                new_p = await k.create_process(
                    str(getattr(self, "_agent_key", None) or "main"),
                    session_id=str(_sid) if _sid else None,
                    capabilities=caps,
                    token_budget=budget,
                    meta=meta,
                    intent=intent,
                )
                if profile and hasattr(k, "_call"):
                    try:
                        k._call(
                            "coding_profile_apply",
                            {"process_id": new_p.id, "profile": profile},
                        )
                        refreshed = k.get_process(new_p.id)
                        if refreshed is not None:
                            new_p = refreshed
                    except Exception:
                        pass
            try:
                used = int(getattr(old, "tokens_used", 0) or 0)
                if used > 0 and hasattr(k, "charge_tokens"):
                    k.charge_tokens(new_p.id, min(used, int(budget or used)))
            except Exception:
                pass
            try:
                if hasattr(k, "mark_running"):
                    await k.mark_running(new_p.id)
            except Exception:
                pass
            # rehydrate 后旧 process 上的 child_proc 租约会 no-op release → 主动清旧 id
            try:
                from backend.kernel.tool_gate import release_orphaned_child_leases

                old_id = str(getattr(old, "id", "") or "")
                if old_id and old_id != str(new_p.id):
                    release_orphaned_child_leases(old_id)
                    # 记录映射：工具 finally 可对 old+new 双 release
                    self._rehydrate_lease_prev = old_id
            except Exception as le:
                logger.debug("rehydrate lease cleanup skip: %s", le)
            self._kernel_process = new_p
            self._kernel_host_epoch = int(getattr(k, "_host_epoch", 0) or 0)
            logger.warning(
                "kernel process rehydrated old=%s new=%s epoch=%s caps=%s",
                str(getattr(old, "id", ""))[:12],
                new_p.id[:12],
                self._kernel_host_epoch,
                (getattr(new_p, "capabilities", None) or [])[:8],
            )
            return new_p
        except Exception as re_e:
            logger.error("process rehydrate failed: %s", re_e, exc_info=True)
            return proc

    async def _execute_registered_tool(self, name: str, arguments: dict[str, Any]):
        """统一工具执行入口 → ToolExecutorPort（默认 RegistryToolExecutor）。"""
        # Durable Run：注入 recorder，permission 交互确认可切 WAITING 状态
        arguments = dict(arguments or {})
        arguments.setdefault("_run_recorder", getattr(self, "_run_recorder", None))
        # Agent Computer：agent 身份（主 Agent=main；子代理 loop 实例可自带 key/label）
        arguments.setdefault("_agent_key", getattr(self, "_agent_key", "main"))
        arguments.setdefault("_agent_label", getattr(self, "_agent_label", ""))
        # 联系员工会话：注入 contact 名 + identity id/caps（本员工允许后短路弹窗）
        contact = str(getattr(self, "_contact_agent", "") or "").strip()
        if contact:
            arguments.setdefault("_contact_agent", contact)
            arguments.setdefault("_identity_name", contact)
        if getattr(self, "_identity_id", None):
            arguments.setdefault("_identity_id", str(self._identity_id))
        if getattr(self, "_identity_name", None):
            arguments.setdefault("_identity_name", str(self._identity_name))
        caps = getattr(self, "_identity_capabilities", None)
        if caps is not None:
            arguments.setdefault("_identity_capabilities", list(caps))
        # 编制员工：贯穿工具权限 / 危险命令 / 提权路径
        if getattr(self, "_workforce", False) or str(
            getattr(self, "_agent_key", "") or ""
        ).startswith("wf:"):
            arguments["_workforce"] = True
            if getattr(self, "_identity_id", None):
                arguments.setdefault("_identity_id", str(self._identity_id))
            if getattr(self, "_identity_name", None):
                arguments.setdefault("_identity_name", str(self._identity_name))
            if caps is not None:
                arguments.setdefault("_identity_capabilities", list(caps))
            if getattr(self, "_inbox_item_id", None):
                arguments.setdefault("_inbox_item_id", str(self._inbox_item_id))
            # 员工不走主人确认通道
            arguments["_ws_manager"] = None
        # 真 Sub-Agent：嵌套深度（delegate_task 防失控）
        arguments.setdefault("_subagent_depth", getattr(self, "_subagent_depth", 0))
        # Skill 契约：已挂载包声明 tools 白名单时的执行边界拦截
        blocked = await self._contract_tool_block_reason(name, arguments)
        if blocked:
            return blocked
        # ── Agent Kernel 门控（Hardening）：统一经 tool_gate mediate + charge ──
        # 兼容模式进程（capabilities=None）放行+记录；显式能力集/令牌未授权 →
        # 返回工具级权限错误（反馈给模型，不炸掉整个 run）。
        # 编制路径在 gate deny 后可静默扩权再试；主人路径可 auto escalate。
        from backend.kernel.tool_gate import enforce_tool_gate, release_for_tool

        # Proactive: host may have restarted since last tool — fix process id first
        kernel_proc = await self._ensure_live_kernel_process(arguments)
        # 安全：process_id 只信任 loop 挂载的进程，禁止模型在 arguments 里覆盖
        arguments.pop("_kernel_process_id", None)
        arguments.pop("_process_id", None)
        if kernel_proc is not None:
            arguments["_kernel_process_id"] = kernel_proc.id
        elif getattr(self, "_workforce", False) or str(
            getattr(self, "_agent_key", "") or ""
        ).startswith("wf:"):
            # 编制 run 必须有进程；缺进程直接 fail-closed（不落到无门控执行）
            arguments["_require_kernel_process"] = True

        arguments, gate_err = await enforce_tool_gate(
            name,
            arguments,
            process_id=getattr(kernel_proc, "id", None) if kernel_proc else None,
        )
        # child_proc is a concurrency lease — always release after this call path
        _lease_pid = str(
            arguments.get("_kernel_process_id")
            or (getattr(kernel_proc, "id", None) or "")
        ).strip()
        _child_leased = bool(arguments.get("_child_proc_leased"))

        def _gate_needs_rehydrate(err: str | None) -> bool:
            if not err:
                return False
            low = err.lower()
            return (
                "未知进程" in err
                or "not found" in low
                or "host reconnect" in low
                or "host 重连" in err
                or "rehydrate" in low
                or "closed connection" in low
                or "10053" in err
                or "10054" in err
                or "read timeout" in low
            )

        # Fallback: host wiped / reconnecting — atomic ensure+mediate (one lock)
        # so create_process cannot be separated from mediate by a thrash restart.
        if gate_err and kernel_proc is not None and _gate_needs_rehydrate(gate_err):
            import asyncio

            for attempt in range(1, 4):
                try:
                    if attempt > 1:
                        await asyncio.sleep(0.4 * attempt)
                    from backend.kernel import get_kernel as _gk
                    from backend.kernel.tool_gate import sanitize_args_for_kernel

                    _k = _gk()
                    if hasattr(_k, "ensure_and_mediate"):
                        old = kernel_proc
                        caps = list(getattr(old, "capabilities", None) or []) or None
                        budget = getattr(old, "token_budget", None)
                        meta = dict(getattr(old, "meta", None) or {})
                        meta["rehydrated_from"] = str(getattr(old, "id", "") or "")
                        meta["rehydrate_reason"] = "atomic_ensure_mediate"
                        _sid = (
                            meta.get("session_id")
                            or arguments.get("_session_id")
                            or getattr(self, "_session_id", None)
                        )
                        intent = None
                        if caps:
                            intent = {
                                "goal": "rehydrate after host reconnect",
                                "capabilities": caps,
                                "constraints": {"allow_risky": True},
                            }
                        safe_args = sanitize_args_for_kernel(arguments)
                        new_p, decision = await _k.ensure_and_mediate(
                            str(getattr(old, "id", "") or "") or None,
                            identity=str(
                                getattr(self, "_agent_key", None) or "main"
                            ),
                            capabilities=caps,
                            token_budget=budget,
                            meta=meta,
                            session_id=str(_sid) if _sid else None,
                            action="tool_call",
                            target=name,
                            args=safe_args,
                            intent=intent,
                        )
                        self._kernel_process = new_p
                        self._kernel_host_epoch = int(
                            getattr(_k, "_host_epoch", 0) or 0
                        )
                        arguments["_kernel_process_id"] = new_p.id
                        arguments["_tool_gate_passed"] = True
                        arguments["_tool_gate_internal"] = True
                        kernel_proc = new_p
                        gate_err = None
                        logger.warning(
                            "atomic ensure+mediate ok tool=%s proc=%s→%s allowed=%s",
                            name,
                            str(getattr(old, "id", ""))[:12],
                            new_p.id[:12],
                            decision.allowed,
                        )
                        break
                    # Fallback without atomic helper
                    self._kernel_host_epoch = -1
                    kernel_proc = await self._ensure_live_kernel_process(arguments)
                    if kernel_proc is None:
                        continue
                    arguments["_kernel_process_id"] = kernel_proc.id
                    arguments.pop("_tool_gate_passed", None)
                    arguments.pop("_tool_gate_internal", None)
                    arguments, gate_err = await enforce_tool_gate(
                        name,
                        arguments,
                        process_id=kernel_proc.id,
                    )
                    if not gate_err or not _gate_needs_rehydrate(gate_err):
                        break
                except Exception as re_e:
                    from backend.kernel import KernelPermissionError as _KPE

                    if isinstance(re_e, _KPE):
                        # Real capability deny — stop retrying reconnect path
                        gate_err = f"Error: Kernel 权限拒绝——{re_e}"
                        break
                    logger.error(
                        "atomic ensure+mediate failed attempt=%s: %s",
                        attempt,
                        re_e,
                    )
                    gate_err = (
                        f"Error: Kernel host 重连中——{type(re_e).__name__}: "
                        f"{str(re_e)[:160]}"
                    )
        # Reconnect / wiped-process errors are not capability denies — do not
        # burn escalation budget on dead process ids.
        if gate_err and _gate_needs_rehydrate(gate_err):
            if _child_leased:
                release_for_tool(name, _lease_pid)
            return gate_err
        if gate_err and "Kernel 权限拒绝" in gate_err and kernel_proc is not None:
            # ── 编制 / 主人 提权回退（gate 只做裁决；扩权逻辑仍在 loop）──
            from backend.kernel import KernelPermissionError, get_kernel

            e_msg = gate_err.replace("Error: Kernel 权限拒绝——", "", 1)
            e = KernelPermissionError(e_msg)
            logger.warning(
                "kernel 拦截工具调用 tool=%s proc=%s: %s",
                name,
                kernel_proc.id,
                e,
            )
            agent_key = str(getattr(self, "_agent_key", "") or "")
            is_wf = agent_key.startswith("wf:") or bool(
                getattr(self, "_workforce", False)
            )
            if is_wf:
                try:
                    from backend.agent.grant_store import tool_matches_crew_caps
                    from backend.agent.steward_permission import (
                        load_identity_capabilities,
                    )

                    # 始终从档案重载：grant_caps 后当单要立刻吃到新权
                    caps = list(getattr(self, "_identity_capabilities", None) or [])
                    try:
                        fresh_caps = (
                            await load_identity_capabilities(
                                str(getattr(self, "_identity_id", "") or "") or None
                            )
                        ) or []
                        if fresh_caps:
                            # 合并：档案 ∪ 本轮快照（扩权后档案更大）
                            caps = list(dict.fromkeys([*caps, *fresh_caps]))
                            self._identity_capabilities = caps  # type: ignore[misc]
                    except Exception:
                        pass
                    if tool_matches_crew_caps(name, caps):
                        # H2-B5: no local capabilities |=  — only escalate / re-issue via kernel
                        try:
                            k = get_kernel()
                            from backend.agent.grant_store import crew_cap_for_tool

                            want = crew_cap_for_tool(name) or name
                            esc = None
                            if hasattr(k, "request_escalation"):
                                esc = await k.request_escalation(
                                    kernel_proc.id,
                                    [want],
                                    reason=f"workforce identity grant for tool {name}",
                                )
                            # Auto-approve only when approval rules allow (kernel side)
                            if esc is not None and hasattr(k, "approve_escalation"):
                                try:
                                    from backend.core.config import settings as _st

                                    auto = bool(
                                        getattr(
                                            _st,
                                            "agent_kernel_auto_escalate",
                                            True,
                                        )
                                    )
                                    if auto and getattr(esc, "status", "") == "pending":
                                        await k.approve_escalation(
                                            getattr(esc, "id", ""),
                                            by="system:workforce_identity",
                                        )
                                        logger.info(
                                            "workforce escalate+approve tool=%s cap=%s proc=%s",
                                            name,
                                            want,
                                            kernel_proc.id,
                                        )
                                except Exception as ae:
                                    logger.debug("workforce auto-approve skip: %s", ae)
                        except Exception as se:
                            logger.debug("workforce escalate path skip: %s", se)
                        # 清掉 passed/internal 标记后强制再 gate 一次（含 charge）
                        arguments.pop("_tool_gate_passed", None)
                        arguments.pop("_tool_gate_internal", None)
                        arguments, gate_err2 = await enforce_tool_gate(
                            name,
                            arguments,
                            process_id=kernel_proc.id,
                        )
                        if gate_err2:
                            if _child_leased or arguments.get("_child_proc_leased"):
                                release_for_tool(name, _lease_pid or kernel_proc.id)
                            return (
                                f"{gate_err2}"
                                "（编制已走提权通道仍失败；请 CEO 在 /approvals 批准）"
                                if "权限拒绝" in gate_err2
                                else gate_err2
                            )
                        # re-gate may have taken a fresh child_proc lease
                        _child_leased = bool(
                            arguments.get("_child_proc_leased") or _child_leased
                        )
                        _lease_pid = str(
                            arguments.get("_kernel_process_id") or kernel_proc.id
                        )
                        # fall through
                    else:
                        if _child_leased:
                            release_for_tool(name, _lease_pid or kernel_proc.id)
                        return (
                            f"Error: 编制策略拒绝工具 «{name}»（不在员工能力档案内）。"
                            "请主人让 CEO 在权限看板扩权，不要对每一次工具点「允许」。"
                        )
                except Exception as se:
                    logger.debug("workforce steward escalate path: %s", se)
                    if _child_leased:
                        release_for_tool(name, _lease_pid or kernel_proc.id)
                    return (
                        f"Error: Kernel 权限拒绝——{e}。"
                        "员工路径不向主人发起提权审批。"
                    )
            else:
                esc_note = ""
                if bool(getattr(settings, "agent_kernel_auto_escalate", True)):
                    try:
                        req = await get_kernel().request_escalation(
                            kernel_proc.id,
                            [name],
                            reason=f"工具调用被能力集拦截：{name}",
                        )
                        esc_note = (
                            f"（已自动发起权限申请 {req.id}，"
                            "用户在权限控制台批准后即可重试；请勿重复调用本工具）"
                        )
                    except ValueError:
                        pass
                    except Exception:
                        pass
                if _child_leased:
                    release_for_tool(name, _lease_pid or kernel_proc.id)
                return f"Error: Kernel 权限拒绝——{e}{esc_note}"
        elif gate_err:
            if _child_leased:
                release_for_tool(name, _lease_pid)
            return gate_err
        # ── 重复搜索软干预（0.4.4：研究任务收敛刹车）──
        # 同 run 内同查询重复：第 2 次结果前附提醒；第 3 次起直接拒绝执行，
        # 强制模型基于已有信息总结（prompt 层刹车之外的工程层兜底）。
        try:
            repeat_verdict = self._search_repeat_verdict(name, arguments)
            if repeat_verdict == "block":
                logger.info("重复搜索拦截 tool=%s query=%s", name, str(arguments)[:120])
                total = int(getattr(self, "_search_total_calls", 0) or 0)
                max_run = int(getattr(settings, "agent_search_max_per_run", 8) or 8)
                if max_run > 0 and total > max_run:
                    return (
                        f"Error: 本轮研究已累计搜索 {total} 次（上限 {max_run}）。"
                        "继续搜索收益极低——请立即基于已收集内容总结交付；"
                        "缺口请在答案中显式列出，勿再调用搜索类工具。"
                    )
                return (
                    "Error: 检测到同一/近似查询已执行 3 次以上——继续重复搜索不会带来新信息。"
                    "请立即基于已收集的内容总结交付；如有未覆盖的缺口，在答案中显式注明，"
                    "或改用**角度完全不同**的新查询（而非同义改写）。"
                )
            repeat_prefix = (
                "[提醒] 该查询此前已执行过，结果大概率相同。若本次结果无新增事实，"
                "请停止继续搜索并进入总结阶段。\n\n" if repeat_verdict == "warn" else ""
            )
            ex = getattr(self, "tool_executor", None)
            if ex is not None:
                result = await ex.execute(name, arguments)
                return (
                    repeat_prefix + result
                    if repeat_prefix and isinstance(result, str)
                    else result
                )
            from backend.tools.registry import ToolRegistry as UnifiedToolRegistry

            result = await UnifiedToolRegistry.execute(name, arguments)
            return (
                repeat_prefix + result
                if repeat_prefix and isinstance(result, str)
                else result
            )
        finally:
            # Always free child_proc concurrency lease after tool path completes
            if _child_leased or arguments.get("_child_proc_leased"):
                prev = str(getattr(self, "_rehydrate_lease_prev", "") or "")
                also = [prev] if prev else None
                release_for_tool(
                    name,
                    _lease_pid or arguments.get("_kernel_process_id"),
                    also_process_ids=also,
                )
                arguments.pop("_child_proc_leased", None)
                if prev:
                    try:
                        delattr(self, "_rehydrate_lease_prev")
                    except Exception:
                        self._rehydrate_lease_prev = None

    # ── 重复搜索检测（收敛刹车 + 全局预算 + 近似同义）──────────

    _SEARCH_TOOL_NAMES = frozenset({
        "web_search", "x_search", "search", "websearch",
        "web_extract", "web_fetch", "fetch_url", "fetch_webpage",
        "browse_page", "open_page", "tavily_search", "duckduckgo_search",
    })

    def _search_repeat_verdict(self, name: str, arguments: dict[str, Any]) -> str | None:
        """返回 None（放行）/ "warn" / "block"。

        1) 单 run 搜索总次数 > agent_search_max_per_run → block
        2) 精确/词序归一指纹：第 2 次 warn，第 3 次起 block
        3) 与历史 query 词集 Jaccard ≥ 阈值 → 同一桶
        """
        if not bool(getattr(settings, "agent_search_repeat_guard", True)):
            return None
        if name not in self._SEARCH_TOOL_NAMES:
            return None
        query = str(
            arguments.get("query")
            or arguments.get("q")
            or arguments.get("url")
            or arguments.get("search_term")
            or ""
        ).strip().lower()

        import hashlib

        max_run = int(getattr(settings, "agent_search_max_per_run", 8) or 8)

        if not query:
            total = int(getattr(self, "_search_total_calls", 0) or 0) + 1
            self._search_total_calls = total
            if max_run > 0 and total > max_run:
                return "block"
            return None

        tokens = [
            tok
            for tok in query.replace(",", " ").replace("，", " ").replace("、", " ").split()
            if tok
        ]
        normalized = " ".join(sorted(tokens))
        fp = hashlib.sha1(f"{name}:{normalized}".encode("utf-8")).hexdigest()[:12]

        jaccard_thr = float(getattr(settings, "agent_search_similar_jaccard", 0.72) or 0.72)
        token_set = set(tokens)
        seen_sets: list = getattr(self, "_search_token_sets", None) or []
        matched_fp = None
        if token_set and jaccard_thr > 0:
            for old_fp, old_set in seen_sets:
                if not old_set:
                    continue
                inter = len(token_set & old_set)
                union = len(token_set | old_set) or 1
                if inter / union >= jaccard_thr:
                    matched_fp = old_fp
                    break
        if matched_fp is None:
            seen_sets.append((fp, token_set))
            self._search_token_sets = seen_sets[-40:]
            use_fp = fp
        else:
            use_fp = matched_fp

        counter = getattr(self, "_search_fp_counter", None)
        if counter is None:
            counter = {}
            self._search_fp_counter = counter
        count = counter.get(use_fp, 0) + 1
        counter[use_fp] = count

        total = int(getattr(self, "_search_total_calls", 0) or 0) + 1
        self._search_total_calls = total

        if max_run > 0 and total > max_run:
            return "block"
        if count >= 3:
            return "block"
        if count == 2 or (max_run > 0 and total >= max(3, max_run - 2)):
            return "warn"
        return None

    # ── Kernel iteration gate（Phase 2：Alpha Review #1 融合）──────────


    async def _get_rag_service(self):
        """懒加载 RAG 服务。未配 Embedding+Qdrant 时为 Null（本地模式）。"""
        if self._rag_service is None:
            try:
                from backend.services.rag.capability import use_vector_rag
                from backend.services.rag.factory import RAGServiceFactory

                # 本地模式也返回 Null 实例，避免反复探测；向量模式返回 Qdrant
                self._rag_service = RAGServiceFactory.get_service()
                if not use_vector_rag():
                    # 标记：自动注入路径会再检查 capability
                    pass
            except Exception as e:
                logger.warning(f"RAG service unavailable: {e}")
        return self._rag_service

    def _append_to_system(self, messages: list[dict[str, Any]], block: str) -> None:
        if not block or not block.strip():
            return
        found = False
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "system":
                messages[i]["content"] = (messages[i].get("content") or "") + "\n\n" + block
                found = True
                break
        if not found:
            messages.insert(0, {"role": "system", "content": block})

    async def _inject_rag_context(
        self,
        messages: list[dict[str, Any]],
        user_input: str,
        *,
        top_k: int = 3,
        strengthen: bool = False,
        min_score: float | None = None,
    ) -> list[dict[str, Any]]:
        """向量 RAG 自动注入：仅 Embedding+Qdrant 就绪时生效（默认本地模式跳过）。"""
        from backend.services.rag.capability import get_rag_status

        st = get_rag_status()
        if not st.auto_inject:
            logger.debug("RAG auto-inject skipped: %s", st.reason[:100])
            return messages

        rag = await self._get_rag_service()
        if rag is None:
            return messages

        k = top_k * 2 if strengthen else top_k
        try:
            context = await rag.search_knowledge_base(
                user_input,
                top_k=k,
                user_id=str(self.user_id) if self.user_id else None,
                min_score=min_score,
            )
            # Null 实现会返回“不可用”文案 — 不应注入
            if context and context.strip() and "知识库检索不可用" not in context:
                logger.info(
                    f"Injected RAG context ({len(context)} chars) top_k={k} for: {user_input[:50]}"
                )
                self._append_to_system(messages, f"# 相关知识（RAG）\n{context}")
        except Exception as e:
            logger.warning(f"RAG context injection failed (degraded to local): {e}")

        # ── Workforce 身份记忆召回（Alpha Review #4）──
        # 工单执行中按当前输入检索身份记忆（prompt 硬注入之外的执行期召回：
        # 中期任务上下文漂移后，相关经验/方法论仍能按当前输入浮现）
        agent_key = getattr(self, "_agent_key", "") or ""
        if agent_key.startswith("wf:"):
            try:
                identity_id = agent_key[3:]
                mem_docs = await rag.search_identity_memory(
                    user_input, identity_id, top_k=3
                )
                if mem_docs:
                    block = "# 身份记忆召回（与当前输入相关）\n" + "\n".join(
                        f"- [{(d.payload or {}).get('kind', 'memory')}] {d.text}"
                        for d in mem_docs
                    )
                    self._append_to_system(messages, block)
                    logger.info(
                        "Injected identity memory recall (%d docs) for wf:%s",
                        len(mem_docs), identity_id[:8],
                    )
            except Exception as e:
                logger.debug("identity memory recall skipped: %s", e)

        return messages

    async def _inject_wiki_context(
        self,
        messages: list[dict[str, Any]],
        user_input: str,
        *,
        limit: int = 6,
        min_score: float = 0.2,
    ) -> list[dict[str, Any]]:
        """把 Wiki 图谱中匹配的实体摘要拼进 system（简单相关度门槛）。"""
        q = (user_input or "").strip()
        if len(q) < 2:
            return messages
        try:
            from backend.repositories.wiki_repo import AsyncWikiEntityRepository

            repo = AsyncWikiEntityRepository()
            ents = await repo.search(q) or []
            if not ents:
                return messages
            lim = max(1, min(int(limit or 6), 12))
            q_low = q.lower()
            q_tokens = {t for t in q_low.replace("/", " ").replace("-", " ").split() if len(t) >= 2}

            def _score(ent: object) -> float:
                name = str(getattr(ent, "name", "") or "")
                desc = str(getattr(ent, "description", "") or "")
                hay = f"{name} {desc}".lower()
                if not hay.strip():
                    return 0.0
                sc = 0.0
                if name and name.lower() in q_low:
                    sc += 0.7
                if q_low and name.lower() and name.lower() in q_low:
                    sc += 0.2
                # token overlap
                n_toks = {t for t in hay.replace(",", " ").split() if len(t) >= 2}
                if q_tokens and n_toks:
                    inter = q_tokens & n_toks
                    sc += 0.5 * (len(inter) / max(1, len(q_tokens)))
                # CJK bigram soft
                for i in range(max(0, len(q) - 1)):
                    bg = q[i : i + 2]
                    if bg.strip() and bg in hay:
                        sc += 0.08
                return sc

            ranked = sorted((( _score(e), e) for e in ents), key=lambda x: -x[0])
            kept = [(s, e) for s, e in ranked if s >= float(min_score)][:lim]
            if not kept:
                logger.info(
                    "Wiki inject skipped: all below min_score=%.2f (candidates=%s)",
                    min_score,
                    len(ents),
                )
                return messages
            lines = ["# Wiki 图谱相关实体"]
            for s, e in kept:
                lines.append(
                    f"- **{e.name}** ({getattr(e, 'entity_type', 'concept')})"
                    + (f"：{e.description}" if e.description else "")
                    + f" (rel={s:.2f})"
                )
            self._append_to_system(messages, "\n".join(lines))
            logger.info("Injected %s wiki entities for query", len(kept))
        except Exception as e:
            logger.debug("Wiki inject skipped: %s", e)
        return messages

