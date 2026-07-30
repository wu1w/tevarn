"""编制记忆：统一注入（Assembler）与沉淀（Writer）。

不新建记忆表 — 只读写 IdentityRegistry / IdentityMemoryEntry。
读优先级：Identity Memory ≫ session ≫ entities/wiki/graph（后三者不在此模块）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

STICKY_KINDS = ("persona", "duty", "methodology", "preference")
TOMBSTONE_MARKERS = ("（已废止）", "(已废止)", "[tombstone]", "[superseded]")

Mode = Literal["workforce", "chat", "preview", "compact"]


@dataclass
class MemoryEntryRef:
    id: str
    kind: str
    version: int = 1
    chars: int = 0


@dataclass
class MemoryInjectResult:
    header: str
    body: str
    entries_used: list[MemoryEntryRef] = field(default_factory=list)
    truncated: bool = False
    token_estimate: int = 0
    mode: str = "workforce"


def _settings_int(name: str, default: int) -> int:
    try:
        from backend.core.config import settings

        return int(getattr(settings, name, default) or default)
    except Exception:
        return default


def _settings_bool(name: str, default: bool) -> bool:
    try:
        from backend.core.config import settings

        return bool(getattr(settings, name, default))
    except Exception:
        return default


def _clip(text: str, n: int) -> str:
    t = (text or "").replace("\r", "").strip()
    if len(t) <= n:
        return t
    return t[: max(0, n - 1)] + "…"


def _is_tombstone(content: str) -> bool:
    c = (content or "").strip()
    if not c:
        return True
    for m in TOMBSTONE_MARKERS:
        if c == m or c.startswith(m):
            return True
    return False


def _entry_id(m: Any) -> str:
    return str(getattr(m, "id", "") or "")


def _kind(m: Any) -> str:
    return str(getattr(m, "kind", "") or "")


def _content(m: Any) -> str:
    return str(getattr(m, "content", "") or "")


def _version(m: Any) -> int:
    try:
        return int(getattr(m, "version", 1) or 1)
    except (TypeError, ValueError):
        return 1


def _keyword_score(text: str, query: str) -> int:
    """极简相关度：query 词出现次数。"""
    if not query or not text:
        return 0
    tokens = [t for t in re.split(r"[\s,，。；;、/\\]+", query.lower()) if len(t) >= 2]
    if not tokens:
        return 0
    low = text.lower()
    return sum(1 for t in tokens if t in low)


class CrewMemoryAssembler:
    """身份记忆注入块组装（编制 / 联系 TA / 预览共用）。"""

    def __init__(self, registry: Any | None = None) -> None:
        self._registry = registry

    def _reg(self) -> Any:
        if self._registry is not None:
            return self._registry
        try:
            from backend.kernel import get_kernel

            k = get_kernel()
            reg = getattr(k, "identity_registry", None)
            if reg is not None:
                return reg
        except Exception:
            k = None
        # 兜底：kernel 未挂 registry 时按标准方式自建（kernel + AsyncSessionLocal）。
        # 不能无参构造——IdentityRegistry.__init__ 必填 kernel 与 session_factory。
        from backend.database import AsyncSessionLocal
        from backend.kernel.identity import IdentityRegistry

        return IdentityRegistry(k, AsyncSessionLocal)

    async def _select_experiences(
        self,
        experiences: list[Any],
        *,
        instruction: str,
        identity_id: Any,
        exp_max: int,
    ) -> list[Any]:
        """experience 超 cap 时挑选：向量 top-k（对齐 SQLite）→ 关键词 → 最新。

        硬约束：只返回 current_memory 里仍生效、非 tombstone 的条目；
        禁止只拿向量原文跳过版本链。
        """
        if exp_max <= 0 or not experiences:
            return []
        if len(experiences) <= exp_max:
            return experiences

        by_id: dict[str, Any] = {}
        for m in experiences:
            eid = _entry_id(m)
            if eid:
                by_id[eid] = m

        # 1) 向量 RAG（可选）
        if instruction.strip():
            try:
                from backend.services.rag.capability import use_vector_rag

                if use_vector_rag():
                    from backend.services.rag.factory import RAGServiceFactory

                    rag = RAGServiceFactory.get_service()
                    docs = await rag.search_identity_memory(
                        instruction,
                        str(identity_id),
                        top_k=max(exp_max * 3, exp_max),  # 多取再过滤 kind
                    )
                    ordered: list[Any] = []
                    seen: set[str] = set()
                    for d in docs or []:
                        payload = getattr(d, "payload", None) or {}
                        if not isinstance(payload, dict):
                            payload = {}
                        # 只收 experience（向量库可能混有 sticky 投影）
                        kind = str(payload.get("kind") or "")
                        eid = str(
                            payload.get("entry_id")
                            or getattr(d, "id", "")
                            or ""
                        )
                        if not eid or eid in seen:
                            continue
                        m = by_id.get(eid)
                        if m is None:
                            continue
                        if kind and kind != "experience" and _kind(m) != "experience":
                            continue
                        if _kind(m) != "experience":
                            continue
                        if _is_tombstone(_content(m)):
                            continue
                        ordered.append(m)
                        seen.add(eid)
                        if len(ordered) >= exp_max:
                            break
                    if ordered:
                        logger.debug(
                            "crew_memory experience via vector top-k n=%s",
                            len(ordered),
                        )
                        return ordered
            except Exception as e:
                logger.debug("crew_memory vector select skip: %s", e)

        # 2) 关键词
        if instruction.strip():
            scored = sorted(
                experiences,
                key=lambda m: (
                    _keyword_score(_content(m), instruction),
                    _version(m),
                ),
                reverse=True,
            )
            return scored[:exp_max]

        # 3) 最新
        return experiences[:exp_max]

    async def build_inject_block(
        self,
        identity_id: Any,
        instruction: str = "",
        *,
        mode: Mode = "workforce",
        memory_entries: list[Any] | None = None,
    ) -> MemoryInjectResult:
        """组装注入块。memory_entries 可选（dispatcher 已拉取时可传入避免双查）。"""
        if memory_entries is None:
            try:
                memory_entries = await self._reg().current_memory(identity_id)
            except Exception as e:
                logger.debug("crew_memory load failed: %s", e)
                memory_entries = []

        header = "## 你的身份记忆（人格/职责）"
        if not memory_entries:
            return MemoryInjectResult(
                header=header,
                body="（暂无身份记忆）",
                mode=mode,
            )

        # compact：配额紧时只 persona+duty
        if mode == "compact":
            sticky_kinds = ("persona", "duty")
            exp_max = 0
        else:
            sticky_kinds = STICKY_KINDS
            exp_max = _settings_int("crew_memory_experience_max_inject", 2)
            if mode == "chat":
                exp_max = min(exp_max, _settings_int("crew_memory_experience_max_inject_chat", 1))

        exp_chars = _settings_int("crew_memory_experience_max_chars", 800)
        sticky_chars = 2000 if mode in ("chat", "preview") else 280
        sticky_cap = 6
        other_cap = 2
        total_text_cap = 1500 if mode == "workforce" else 2400
        full_max = _settings_int("agent_identity_memory_full_inject_max", 8)

        sticky: list[Any] = []
        experiences: list[Any] = []
        other: list[Any] = []
        for m in memory_entries:
            if _is_tombstone(_content(m)):
                continue
            k = _kind(m)
            if k in sticky_kinds:
                sticky.append(m)
            elif k == "experience":
                experiences.append(m)
            else:
                other.append(m)

        # experience：倒序（新优先），cap
        # 超 cap 时：优先向量 top-k（须对齐 SQLite current + 非 tombstone），回落关键词
        experiences = list(reversed(experiences))  # current_memory 通常旧→新
        if exp_max <= 0:
            experiences = []
        elif len(experiences) > exp_max:
            picked = await self._select_experiences(
                experiences,
                instruction=instruction or "",
                identity_id=identity_id,
                exp_max=exp_max,
            )
            experiences = picked

        def line(m: Any, n: int) -> str:
            body = _clip(_content(m).replace("\n", " "), n)
            return f"- [{_kind(m) or 'memory'}] {body}"

        lines: list[str] = []
        used: list[MemoryEntryRef] = []
        truncated = False

        for m in sticky[:sticky_cap]:
            lines.append(line(m, sticky_chars))
            used.append(
                MemoryEntryRef(
                    id=_entry_id(m),
                    kind=_kind(m),
                    version=_version(m),
                    chars=len(_content(m)),
                )
            )
        if len(sticky) > sticky_cap:
            truncated = True

        for m in experiences:
            lines.append(line(m, exp_chars if mode != "workforce" else min(exp_chars, 160)))
            used.append(
                MemoryEntryRef(
                    id=_entry_id(m),
                    kind="experience",
                    version=_version(m),
                    chars=len(_content(m)),
                )
            )

        for m in other[:other_cap]:
            lines.append(line(m, 160))
            used.append(
                MemoryEntryRef(
                    id=_entry_id(m),
                    kind=_kind(m),
                    version=_version(m),
                    chars=len(_content(m)),
                )
            )
        if len(other) > other_cap:
            truncated = True

        # 总条目超 full_max：砍 other → experience（sticky 保）
        if len(used) > full_max:
            keep_sticky = [u for u in used if u.kind in sticky_kinds]
            keep_rest = [u for u in used if u.kind not in sticky_kinds]
            budget = max(0, full_max - len(keep_sticky))
            keep_rest = keep_rest[:budget]
            keep_ids = {u.id for u in keep_sticky + keep_rest}
            # 重建 lines
            lines = []
            used = []
            for m in sticky[:sticky_cap]:
                if _entry_id(m) in keep_ids or not keep_ids:
                    lines.append(line(m, sticky_chars))
                    used.append(
                        MemoryEntryRef(
                            id=_entry_id(m), kind=_kind(m), version=_version(m)
                        )
                    )
            for m in experiences + other:
                if _entry_id(m) in keep_ids:
                    n = exp_chars if _kind(m) == "experience" else 160
                    if mode == "workforce" and _kind(m) == "experience":
                        n = min(n, 160)
                    lines.append(line(m, n))
                    used.append(
                        MemoryEntryRef(
                            id=_entry_id(m), kind=_kind(m), version=_version(m)
                        )
                    )
            truncated = True

        text = "\n".join(lines) if lines else "（暂无身份记忆）"
        if len(text) > total_text_cap:
            text = text[: total_text_cap - 1] + "…"
            truncated = True

        # 粗估 token
        token_est = max(1, round(len(text) / 3.4))
        return MemoryInjectResult(
            header=header,
            body=text,
            entries_used=used,
            truncated=truncated,
            token_estimate=token_est,
            mode=mode,
        )


class CrewMemoryWriter:
    """身份记忆写入门禁：失败不沉淀、自动沉淀默关、distilled 需审批。"""

    def __init__(self, registry: Any | None = None) -> None:
        self._registry = registry

    def _reg(self) -> Any:
        if self._registry is not None:
            return self._registry
        try:
            from backend.kernel import get_kernel

            k = get_kernel()
            reg = getattr(k, "identity_registry", None)
            if reg is not None:
                return reg
        except Exception:
            k = None
        # 兜底：kernel 未挂 registry 时按标准方式自建（kernel + AsyncSessionLocal）。
        # 不能无参构造——IdentityRegistry.__init__ 必填 kernel 与 session_factory。
        from backend.database import AsyncSessionLocal
        from backend.kernel.identity import IdentityRegistry

        return IdentityRegistry(k, AsyncSessionLocal)

    @staticmethod
    def should_skip_distill(
        *,
        status: str,
        result: str,
        force: bool = False,
    ) -> str | None:
        """返回跳过原因；None=可写。"""
        st = (status or "").lower().strip()
        res = result or ""
        if not force:
            if st not in ("done", "completed", "success", "ok"):
                return f"status={st or 'empty'}"
            if any(
                x in res
                for x in (
                    "[Budget Exceeded]",
                    "预算耗尽",
                    "预算不足",
                    "[Stopped]",
                    "[Cancelled]",
                    "Generation was cancelled",
                    "[Timeout]",
                    "超时",
                    "grounding block",
                    "[Error]",
                )
            ):
                return "failure_marker_in_result"
            min_chars = _settings_int("crew_memory_auto_distill_min_chars", 200)
            if len(res.strip()) < min_chars:
                return "result_too_short"
            if not _settings_bool("crew_memory_auto_distill", False):
                return "auto_distill_disabled"
        else:
            # 手动：仍拒绝明确失败标记
            if any(
                x in res
                for x in (
                    "[Budget Exceeded]",
                    "预算耗尽",
                    "预算不足",
                )
            ):
                return "failure_marker_in_result"
        return None

    async def maybe_distill_from_job(
        self,
        *,
        identity_id: Any,
        instruction: str,
        result: str,
        process_id: str | None = None,
        status: str = "done",
        force: bool = False,
        approved_by: str | None = None,
        source: str | None = None,
    ) -> Any | None:
        """完工沉淀 experience。force=True 为手动 distill（忽略 auto 开关）。

        返回写入的 entry 或 None（跳过）。不向调用方抛业务跳过。
        """
        if not identity_id:
            return None
        reason = self.should_skip_distill(status=status, result=result or "", force=force)
        if reason:
            logger.debug(
                "crew_memory skip distill identity=%s reason=%s",
                str(identity_id)[:8],
                reason,
            )
            return None

        require_approve = _settings_bool("crew_memory_require_approve_distill", True)
        # 自动路径：若要求审批且无 approved_by → 跳过（v1 不写 pending 表）
        if not force and require_approve and not approved_by:
            logger.debug("crew_memory skip auto distill: require approve")
            return None

        instr = _clip(instruction, 160)
        body = _clip(result, 220)
        content = (
            f"[{status}] 工单完成。\n"
            f"任务：{instr or '（无指令）'}\n"
            f"结果摘要：{body or '（无结果）'}"
        )
        if process_id:
            content += f"\nprocess={str(process_id)[:12]}"

        # 手动：manual；自动且有审批：distilled；否则 system
        if force:
            write_source = source or "manual"
            ab = approved_by or "owner"
        elif approved_by:
            write_source = "distilled"
            ab = approved_by
        else:
            write_source = "system"
            ab = None

        try:
            entry = await self._reg().add_memory(
                identity_id,
                "experience",
                content,
                source=write_source,
                approved_by=ab,
            )
            logger.info(
                "crew_memory distilled identity=%s process=%s force=%s",
                str(identity_id)[:8],
                (process_id or "")[:8],
                force,
            )
            return entry
        except Exception as e:
            logger.debug("crew_memory distill failed: %s", e)
            return None

    async def record_manual(
        self,
        identity_id: Any,
        kind: str,
        content: str,
        *,
        approved_by: str,
        source: str = "manual",
    ) -> Any:
        return await self._reg().add_memory(
            identity_id, kind, content, source=source, approved_by=approved_by
        )

    async def supersede(
        self, entry_id: Any, new_content: str, *, approved_by: str
    ) -> Any:
        return await self._reg().supersede_memory(
            entry_id, new_content, approved_by=approved_by
        )

    async def retire(self, entry_id: Any, *, approved_by: str) -> Any:
        """废止：用 tombstone 文案 supersede，Assembler 不再注入。"""
        return await self.supersede(
            entry_id, "（已废止）", approved_by=approved_by
        )


# 模块级便捷
_assembler: CrewMemoryAssembler | None = None
_writer: CrewMemoryWriter | None = None


def get_crew_memory_assembler(registry: Any | None = None) -> CrewMemoryAssembler:
    global _assembler
    if registry is not None:
        return CrewMemoryAssembler(registry)
    if _assembler is None:
        _assembler = CrewMemoryAssembler()
    return _assembler


def get_crew_memory_writer(registry: Any | None = None) -> CrewMemoryWriter:
    global _writer
    if registry is not None:
        return CrewMemoryWriter(registry)
    if _writer is None:
        _writer = CrewMemoryWriter()
    return _writer
