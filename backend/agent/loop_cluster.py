"""Loop cluster/complexity mixin (Phase 2.4 split from loop.py)."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from backend.core.config import settings

logger = logging.getLogger(__name__)

# 并发首请求空表自愈：只 load 一次，避免多 session 同时 await load_all_tools
_tools_bootstrap_lock: asyncio.Lock | None = None


def _try_coerce_int(val: Any) -> int | None:
    if isinstance(val, bool) or val is None:
        return None
    if isinstance(val, int):
        return int(val)
    if isinstance(val, float):
        return int(val) if val.is_integer() else None
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            if s.lstrip("+-").isdigit():
                return int(s)
            f = float(s)
            if f.is_integer():
                return int(f)
        except (TypeError, ValueError, OverflowError):
            return None
    return None


def _try_coerce_number(val: Any) -> int | float | None:
    if isinstance(val, bool) or val is None:
        return None
    if isinstance(val, int):
        return int(val)
    if isinstance(val, float):
        return val
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            n = float(s)
        except (TypeError, ValueError, OverflowError):
            return None
        if n.is_integer() and "." not in s and "e" not in s.lower() and "E" not in s:
            try:
                return int(s)
            except (TypeError, ValueError, OverflowError):
                return n
        return n
    return None


def _try_coerce_bool(val: Any) -> bool | None:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        s = val.strip().lower()
        if s in {"true", "yes", "on", "y"}:
            return True
        if s in {"false", "no", "off", "n"}:
            return False
    return None


def _tools_bootstrap_lock_get() -> asyncio.Lock:
    global _tools_bootstrap_lock
    if _tools_bootstrap_lock is None:
        _tools_bootstrap_lock = asyncio.Lock()
    return _tools_bootstrap_lock


class LoopClusterMixin:

    def _pipe_table_score(self, text: str) -> int:
        """Rough count of Markdown table density (pipes on table-like lines)."""
        if not text or "|" not in text:
            return 0
        n = 0
        for line in text.splitlines():
            s = line.strip()
            if s.count("|") >= 2:
                n += s.count("|")
        return n

    def _looks_like_structured_report(self, text: str) -> bool:
        """已是完整报表/表单/Markdown 结构，不应被压成纯文字摘要。"""
        if not text or len(text) < 40:
            return False
        # 明确 Markdown 表格：短稿也要保住
        pipes = self._pipe_table_score(text)
        if pipes >= 6 and ("| ---" in text or "|---" in text or pipes >= 10):
            return True
        if pipes >= 8:
            return True

        score = 0
        headers = text.count("\n## ") + text.count("\n### ") + text.count("## ")
        if headers >= 2:
            score += 2
        if headers >= 1 and len(text) >= 400:
            score += 1
        if text.count("\n- ") + text.count("\n* ") + text.count("\n1.") >= 4:
            score += 1
        if pipes >= 6:
            score += 3
        elif pipes >= 3:
            score += 2
        if "| ---" in text or "|---" in text:
            score += 2
        report_kw = (
            "审计", "报告", "严重", "风险", "建议", "结论", "发现",
            "Critical", "High", "Medium", "Low", "汇总", "报表", "表单",
        )
        if sum(1 for k in report_kw if k in text) >= 2:
            score += 2
        if len(text) >= 800 and headers >= 1:
            score += 1
        return score >= 2

    def _looks_like_multi_answer(self, text: str) -> bool:
        """启发式：模型把多个信源原样并列（非结构化报表）。"""
        if not text or len(text) < 80:
            return False
        # 完整报表常有多级 ## / 表格，不能再当「答案1/2/3」
        if self._looks_like_structured_report(text):
            return False
        markers = [
            "答案1", "答案 1", "答案一", "【答案", "来源1", "来源 1",
            "信源1", "根据工具", "工具1", "结果1", "方案一", "方案1",
            "### 答案", "## 答案", "Answer 1", "Source 1",
            "weather 返回", "web_search 返回", "如下多个",
        ]
        hits = sum(1 for m in markers if m in text)
        if hits >= 1:
            return True
        if text.count("根据") >= 3 and text.count("\n\n") >= 3:
            return True
        return False

    async def _maybe_aggregate_multi_source(
        self,
        *,
        llm_service: Any,
        session_id: uuid.UUID,
        user_input: str,
        draft: str,
        tool_rounds: int,
        last_tool_count: int,
        multi_pending: bool,
    ) -> str:
        """多 *agent* 协同时可选的轻量版式合并；单 agent 永远跳过。

        历史上 last_tool_count>=2 / multi_pending 过宽，会把表格/表单压成纯文字。
        现规则：
        1) 仅 multi_pending（=本 run 用过编制/多 agent 工具）才考虑合并；
        2) 已有表格/结构化报表 → 直接保留草稿；
        3) 合并时强制保留 Markdown 表格与标题，禁止摘要化。
        """
        if not draft or not str(draft).strip():
            return draft

        # 单 agent / 未标记多 agent 协同：绝不二次 LLM 改写
        if not multi_pending:
            return draft

        # 已有表格、表单、多级结构：原样交付（多 agent 亦然）
        if self._looks_like_structured_report(draft):
            logger.info(
                "multi-source skip structured/form session=%s draft=%s pipes=%s",
                session_id,
                len(draft),
                self._pipe_table_score(draft),
            )
            return draft

        # 多 agent 但草稿不像「答案1/2 并列」：无需合并
        if not self._looks_like_multi_answer(draft) and len(draft) >= 200:
            logger.info(
                "multi-source skip non-juxtaposed draft session=%s len=%s",
                session_id,
                len(draft),
            )
            return draft

        if len(draft) < 120:
            return draft

        await self._push_status(session_id, "thinking", "正在整理多 agent 结果（保留版式）…")
        try:
            await self._emit_progress(
                "thinking",
                "整理多员工结果，保留表格与结构…",
            )
        except Exception:
            pass

        sys_p = (
            "你是「版式保留」编辑器，不是摘要器。\n"
            "任务：在保留排版的前提下，整理多 agent/多信源草稿。\n"
            "硬性规则：\n"
            "1) **必须保留** Markdown 表格（| 列）、表头分隔行、标题（##/###）、"
            "有序列表/无序列表、加粗、代码块、引用块；\n"
            "2) **禁止**把表格改写成纯文字段落或「一、二、三」散文；\n"
            "3) **禁止**压缩成两三段摘要；细节、字段、数值尽量保留；\n"
            "4) 多员工结果：用分节标题或合并到同一张大表（追加行），不要删列；\n"
            "5) 仅去掉明显重复段落；冲突时保留更具体者并可一句说明；\n"
            "6) 不要堆砌内部工具名；使用用户语言（通常中文）；\n"
            "7) 只输出最终正文，不要前言。\n"
            "若草稿已有完整表格/表单且只需去重：优先接近原样输出。"
        )
        user_block = (
            "用户问题：\n"
            + str(user_input or "")
            + "\n\n草稿（请保留其中的表格与样式）：\n"
            + str(draft)[:14000]
        )
        msgs = [
            {"role": "system", "content": sys_p},
            {"role": "user", "content": user_block},
        ]
        out = ""
        try:
            async for chunk in llm_service.chat(msgs, tools=None, stream=True):
                if self._should_stop:
                    break
                if chunk.delta:
                    out += chunk.delta
                if chunk.finish_reason:
                    break
        except Exception as e:
            logger.warning("aggregate LLM failed: %s", e)
            return draft

        out = (out or "").strip()
        if len(out) < 8:
            return draft

        # 防表格被吃掉：草稿有表、输出表显著变少 → 保留草稿
        d_pipes = self._pipe_table_score(draft)
        o_pipes = self._pipe_table_score(out)
        if d_pipes >= 6 and o_pipes < max(3, int(d_pipes * 0.5)):
            logger.info(
                "multi-source rejected table-loss session=%s pipes %s -> %s (keep draft)",
                session_id,
                d_pipes,
                o_pipes,
            )
            return draft

        # 防收缩：聚合后明显变短则丢弃（多 agent 亦不可压成干巴摘要）
        if len(draft) >= 300 and len(out) < max(150, int(len(draft) * 0.65)):
            logger.info(
                "multi-source rejected shrink session=%s draft=%s -> out=%s (keep draft)",
                session_id,
                len(draft),
                len(out),
            )
            return draft

        # 结构化被抹平（标题/列表大幅减少）
        if self._looks_like_structured_report(draft) and not self._looks_like_structured_report(out):
            logger.info(
                "multi-source rejected structure-loss session=%s (keep draft)",
                session_id,
            )
            return draft

        logger.info(
            "multi-source aggregated for session %s: draft=%s -> out=%s",
            session_id,
            len(draft),
            len(out),
        )
        return out


    async def _check_auto_optimize(
        self,
        session_id: uuid.UUID,
        config: dict[str, Any],
        total_tokens: int,
    ) -> None:
        """P0-3: 检查是否触发自动优化"""
        if self.ctx_item_repo is None:
            return
        auto_optimize = config.get("auto_optimize", True)
        threshold = config.get("optimize_threshold", 0.7)
        context_window = int(getattr(settings, "context_window", 128_000) or 128_000)

        usage_ratio = total_tokens / max(1, context_window)
        if auto_optimize and usage_ratio > threshold:
            logger.info(
                f"Auto optimize triggered for session {session_id}: "
                f"{usage_ratio:.1%} > {threshold:.1%}"
            )
            try:
                result = await self.ctx_item_repo.optimize(
                    session_id=session_id, threshold=threshold
                )
                logger.info(f"Auto optimize result: {result}")
                await self._push_status(
                    session_id,
                    "optimizing",
                    f"Auto-optimized: freed {result.get('saved_tokens', 0)} tokens",
                )
            except Exception as e:
                logger.warning(f"Auto optimize failed: {e}")

    # 模型不可信的内部键（白名单化：绝不从 tool_call 入参保留）
    # 含 _confirm_ok / _workforce 等——否则 prompt injection 可自授权跳过确认
    _MODEL_FORBIDDEN_ARG_KEYS = frozenset({
        "_tool_gate_passed",
        "_tool_gate_internal",
        "_kernel_process_id",
        "_process_id",
        "_require_kernel_process",
        "_ws_manager",
        "_run_recorder",
        "_confirm_ok",
        "_confirm_ok_source",
        "_workforce",
        "_session_grant",
        "_identity_capabilities",
        "_identity_id",
        "_identity_name",
        "_session_id",
        "_steward_session_id",
        "_user_id",
        "_contact_agent",
        "_inbox_item_id",
        "_agent_key",
        "_agent_label",
        "_subagent_depth",
        "_child_proc_leased",
        "ws_manager",
        "connection_manager",
    })

    def _validate_tool_args(self, schema: dict | None, arguments: dict) -> dict:
        """使用 JSON Schema 校验 tool call 参数。

        始终返回新 dict，避免在原始 tc.arguments 上注入 _ws_manager 等
        导致 WS ToolEvent.model_dump 无法序列化 ConnectionManager。

        安全（P0）：
        - 剥离全部内部 meta 键（模型不可注入 _confirm_ok 等）
        - 若 schema 有 properties，**只保留白名单字段**（防任意键走私）
        服务端 meta 由 loop_tools 在校验后再强制注入。
        """
        raw = dict(arguments) if isinstance(arguments, dict) else {}
        # 1) 剥内部键 + 一切以 _ 开头的键（模型侧）
        cleaned: dict = {}
        for k, v in raw.items():
            ks = str(k)
            if ks in self._MODEL_FORBIDDEN_ARG_KEYS:
                continue
            if ks.startswith("_"):
                continue
            cleaned[ks] = v

        # 2) schema 白名单：只保留 properties 声明的键
        props = None
        if isinstance(schema, dict):
            props = schema.get("properties")
        if isinstance(props, dict) and props:
            allowed = set(props.keys())
            cleaned = {k: v for k, v in cleaned.items() if k in allowed}

        if not schema:
            return cleaned
        # LLM JSON 常把整数写成 "3"；先按 schema 类型软转换，再 clamp / validate
        cleaned = self._coerce_tool_args(schema, cleaned)
        # 软夹紧：数值 min/max 越界时 clamp，减少「max_results=3 < min 5」类无意义失败
        cleaned = self._clamp_tool_args(schema, cleaned)
        try:
            from jsonschema import ValidationError, validate

            validate(instance=cleaned, schema=schema)
        except ImportError:
            pass  # jsonschema未安装时跳过校验
        except ValidationError as e:
            raise ValueError(f"Invalid tool arguments: {e.message}") from e
        return cleaned

    @staticmethod
    def _schema_prop_types(spec: dict) -> set[str]:
        t = spec.get("type")
        if isinstance(t, str) and t:
            return {t}
        if isinstance(t, (list, tuple)):
            return {str(x) for x in t if x}
        return set()

    def _coerce_tool_args(self, schema: dict, cleaned: dict) -> dict:
        """把 LLM 常见的字符串标量收成 schema 声明的 integer/number/boolean。"""
        props = schema.get("properties") if isinstance(schema, dict) else None
        if not isinstance(props, dict) or not cleaned:
            return cleaned
        out = dict(cleaned)
        for key, spec in props.items():
            if key not in out or not isinstance(spec, dict):
                continue
            types = self._schema_prop_types(spec)
            if not types:
                continue
            val = out[key]
            if "integer" in types:
                coerced = _try_coerce_int(val)
                if coerced is not None:
                    out[key] = coerced
                    continue
            if "number" in types:
                coerced = _try_coerce_number(val)
                if coerced is not None:
                    out[key] = coerced
                    continue
            if "boolean" in types:
                coerced = _try_coerce_bool(val)
                if coerced is not None:
                    out[key] = coerced
        return out

    def _clamp_tool_args(self, schema: dict, cleaned: dict) -> dict:
        """按 JSON Schema properties 的 minimum/maximum/minLength 做软夹紧（不改类型）。"""
        props = schema.get("properties") if isinstance(schema, dict) else None
        if not isinstance(props, dict) or not cleaned:
            return cleaned
        out = dict(cleaned)
        for key, spec in props.items():
            if key not in out or not isinstance(spec, dict):
                continue
            val = out[key]
            if isinstance(val, bool):
                continue
            if isinstance(val, (int, float)):
                lo = spec.get("minimum")
                hi = spec.get("maximum")
                try:
                    if lo is not None and val < lo:
                        out[key] = type(val)(lo)
                    if hi is not None and val > hi:
                        out[key] = type(val)(hi)
                except Exception:
                    pass
            elif isinstance(val, str):
                min_len = spec.get("minLength")
                max_len = spec.get("maxLength")
                try:
                    if isinstance(max_len, int) and max_len > 0 and len(val) > max_len:
                        out[key] = val[:max_len]
                    # minLength 不填充假数据，留给校验
                    _ = min_len
                except Exception:
                    pass
        return out

    async def _load_tools(
        self,
        session_id: uuid.UUID,
        enabled_skills: list[str] | None,
        enabled_tools_filter: list[str] | None = None,
        user_input: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        v3.0: 从统一 ToolRegistry 加载工具 schema。

        注意：为了兼容旧 session config，这里同时处理：
        - enabled_skills: 旧配置中的 skill 列表，会映射为工具名称过滤
        - enabled_tools_filter: 旧配置中的 tools 列表
        """
        # 合并名称过滤：旧配置中的 skills 和 tools 都是工具名称
        enabled_names = set()
        if enabled_skills is not None:
            enabled_names.update(enabled_skills)
        if enabled_tools_filter is not None:
            enabled_names.update(enabled_tools_filter)

        # 如果都是 ALL（None）表示不过滤
        filter_names = list(enabled_names) if enabled_names else None

        try:
            from backend.tools.registry import ToolRegistry as UnifiedToolRegistry

            # 非 FastAPI 进程（bench / headless 脚本 / 单测）可能未跑 lifespan，
            # 注册表为空时对 *所有* 供应商都会 has_tools=False。启动期自愈一次。
            if not UnifiedToolRegistry.get_all():
                async with _tools_bootstrap_lock_get():
                    if not UnifiedToolRegistry.get_all():
                        try:
                            from backend.tools.loader import load_all_tools

                            await load_all_tools()
                            logger.warning(
                                "ToolRegistry was empty at _load_tools — called "
                                "load_all_tools() (n=%s). Prefer FastAPI lifespan "
                                "in production.",
                                len(UnifiedToolRegistry.get_all()),
                            )
                        except Exception as load_err:
                            logger.warning(
                                "auto load_all_tools failed: %s", load_err
                            )

            tools = UnifiedToolRegistry.get_tools_schema(filter_names)
            if not tools and filter_names:
                # 过滤后仍空：多半是注册表仍空或名字对不上，记 warning 便于定位
                logger.warning(
                    "get_tools_schema returned 0 tools session=%s filter_n=%s "
                    "registry_n=%s — model will invent tool syntax in text",
                    session_id,
                    len(filter_names),
                    len(UnifiedToolRegistry.get_all()),
                )
            logger.info(
                f"Loaded {len(tools)} unified tools for session {session_id} "
                f"(filter={filter_names})"
            )
        except Exception as e:
            logger.warning(f"Failed to load unified tools: {e}, falling back to old method")
            # 兼容旧方式（lazy import：主路径走 UnifiedToolRegistry）
            from backend.repositories.skill_repo import AsyncSkillRepository
            from backend.repositories.tool_repo import AsyncToolRepository
            from backend.services.tools import ToolRegistry
            from backend.skills import SkillRegistry

            tools = SkillRegistry.get_tools_schema(enabled_skills)
            seen_names = {
                (t.get("function") or {}).get("name")
                for t in tools
                if (t.get("function") or {}).get("name")
            }

            try:
                skill_repo = AsyncSkillRepository()
                active_skills = await skill_repo.get_active_skills()
                for skill in active_skills:
                    if skill.is_builtin:
                        continue
                    if enabled_skills is not None and skill.name not in enabled_skills:
                        continue
                    if skill.name in seen_names:
                        continue
                    tools.append({
                        "type": "function",
                        "function": {
                            "name": skill.name,
                            "description": skill.description or "",
                            "parameters": skill.schema or {"type": "object", "properties": {}},
                        },
                    })
                    seen_names.add(skill.name)
            except Exception as e2:
                logger.warning(f"Failed to load custom skills from DB: {e2}")

            try:
                tool_repo = AsyncToolRepository()
                active_tools = await tool_repo.get_active_tools()
                if enabled_tools_filter is not None:
                    active_tools = [t for t in active_tools if t.name in enabled_tools_filter]
                tool_schemas = ToolRegistry.get_tools_schema(active_tools)
                for ts in tool_schemas:
                    name = (ts.get("function") or {}).get("name")
                    if name and name in seen_names:
                        continue
                    tools.append(ts)
                    if name:
                        seen_names.add(name)
            except Exception as e2:
                logger.warning(f"Failed to load tools from DB: {e2}")

        # 合并 tool-def CtxItem（系统级工具定义）
        if self.ctx_item_repo is not None:
            try:
                tool_defs = await self.ctx_item_repo.list_by_session(
                    session_id=None,
                    scope="system",
                    kind="tool-def",
                    limit=50,
                )
                for td in tool_defs:
                    tools.append({
                        "type": "function",
                        "function": {
                            "name": td.key,
                            "description": td.value[:200],
                            "parameters": {"type": "object", "properties": {}},
                        },
                    })
            except Exception as e:
                logger.warning(f"Failed to load tool-def CtxItems: {e}")

        # Desktop 工具：仅当全量模式或过滤名单显式包含时兜底注入（默认 core 不塞）
        try:
            filter_set = set(enabled_tools_filter) if enabled_tools_filter is not None else None
            if filter_set is None or any(n.startswith("desktop_") for n in filter_set):
                from backend.services.desktop.tools import (
                    DesktopClickTool,
                    DesktopOpenAppTool,
                    DesktopReadFileTool,
                    DesktopScreenshotTool,
                    DesktopScrollTool,
                    DesktopTypeTool,
                    DesktopWriteFileTool,
                )
                desktop_tools = [
                    DesktopScreenshotTool(),
                    DesktopClickTool(),
                    DesktopTypeTool(),
                    DesktopOpenAppTool(),
                    DesktopScrollTool(),
                    DesktopReadFileTool(),
                    DesktopWriteFileTool(),
                ]
                existing_names = {
                    (t.get("function") or {}).get("name")
                    for t in tools
                    if (t.get("function") or {}).get("name")
                }
                for dt in desktop_tools:
                    if filter_set is not None and dt.name not in filter_set:
                        continue
                    if dt.name not in existing_names:
                        tools.append(dt.to_json_schema())
                        logger.info(f"Ensured desktop tool: {dt.name}")
        except Exception as e:
            logger.warning(f"Failed to ensure desktop tools: {e}")

        return tools

    async def _record_flow(
        self,
        session_id: uuid.UUID,
        agent: str,
        accessed_items: list[tuple[str, str]],
        tokens: int,
    ) -> None:
        """记录上下文访问流"""
        if self.context_flow_repo is None:
            return

        # 按 scope 分组
        scope_keys: dict[str, list[str]] = {}
        for scope, key in accessed_items:
            scope_keys.setdefault(scope, []).append(key)

        for scope, keys in scope_keys.items():
            try:
                await self.context_flow_repo.create_flow(
                    session_id=session_id,
                    agent=agent,
                    scope=scope,
                    keys=keys,
                    tokens=tokens,
                )
            except Exception as e:
                logger.warning(f"Failed to record context flow: {e}")

    # ─────────── Auto Cluster Analysis ───────────

    async def _analyze_task_complexity(self, user_input: str) -> float:
        """
        自动分析任务复杂度，返回 0.0-1.0 的分数
        
        高复杂度指标：
        - 多步骤/多领域任务
        - 需要代码 + 分析 + 文档等多种能力
        - 涉及比较、评估、设计等复杂认知
        """
        input_lower = user_input.lower()
        score = 0.0
        
        # 长度指标（长任务通常更复杂）
        if len(user_input) > 200:
            score += 0.2
        elif len(user_input) > 100:
            score += 0.1
        
        # 多步骤关键词（仅实义词；连词类虚词如「和/与/以及/同时」误伤率过高，已移除）
        multi_step_keywords = [
            "分析", "比较", "对比", "评估", "设计", "架构", "规划",
            "实现", "开发", "创建", "构建", "优化", "改进",
            "研究", "调查", "探索", "深入", "详细",
            "多个", "几个", "一系列", "批量", "综合",
        ]
        keyword_count = sum(1 for kw in multi_step_keywords if kw in input_lower)
        score += min(keyword_count * 0.15, 0.4)
        
        # 技术复杂度关键词
        tech_keywords = [
            "代码", "编程", "算法", "数据库", "api", "系统",
            "python", "javascript", "java", "c++", "sql",
            "前端", "后端", "全栈", "部署", "测试", "调试",
            "机器学习", "ai", "模型", "训练", "推理",
            "网络", "安全", "加密", "协议", "服务器",
        ]
        tech_count = sum(1 for kw in tech_keywords if kw in input_lower)
        score += min(tech_count * 0.1, 0.3)
        
        # 输出要求关键词
        output_keywords = [
            "报告", "文档", "方案", "计划", "教程", "指南",
            "总结", "分析结果", "建议", "推荐", "最佳实践",
        ]
        output_count = sum(1 for kw in output_keywords if kw in input_lower)
        score += min(output_count * 0.1, 0.2)
        
        # 问句数量（多个问题通常更复杂）
        question_marks = input_lower.count("?") + input_lower.count("？")
        if question_marks >= 3:
            score += 0.2
        elif question_marks >= 2:
            score += 0.1
        
        # 限制在 0-1 范围
        return min(max(score, 0.0), 1.0)

    async def _auto_create_sub_agents(self, user_input: str, complexity: float) -> list[dict]:
        """
        根据任务内容自动创建子代理配置（复用主会话LLM）
        
        返回子代理信息列表，每个包含:
        - id, name, icon, description, model_ref, system_prompt
        """
        input_lower = user_input.lower()
        sub_agents = []
        
        # 根据任务内容推断需要的专业角色
        roles = []
        
        # 代码/编程相关
        if any(kw in input_lower for kw in ["代码", "编程", "python", "javascript", "java", "c++", "sql", "算法", "调试", "开发", "实现", "bug", "错误", "修复"]):
            roles.append({
                "name": "coder",
                "icon": "💻",
                "description": "专业的编程和代码分析助手",
                "system_prompt": "你是一个专业的编程助手，擅长代码编写、调试和架构设计。请提供具体、可运行的代码示例，并解释关键设计决策。",
            })
        
        # 分析/研究相关
        if any(kw in input_lower for kw in ["分析", "研究", "调查", "比较", "对比", "评估", "数据", "统计", "趋势"]):
            roles.append({
                "name": "analyst",
                "icon": "📊",
                "description": "数据分析和研究专家",
                "system_prompt": "你是一个数据分析专家，擅长逻辑推理、数据解读和趋势分析。请提供结构化的分析框架和清晰的结论。",
            })
        
        # 文档/写作相关
        if any(kw in input_lower for kw in ["报告", "文档", "总结", "写作", "文案", "教程", "指南", "说明"]):
            roles.append({
                "name": "writer",
                "icon": "📝",
                "description": "技术文档和写作专家",
                "system_prompt": "你是一个技术写作专家，擅长将复杂概念转化为清晰易懂的文档。请注重结构、可读性和实用性。",
            })
        
        # 设计/架构相关
        if any(kw in input_lower for kw in ["设计", "架构", "规划", "方案", "系统", "框架", "模式"]):
            roles.append({
                "name": "architect",
                "icon": "🏗️",
                "description": "系统架构和设计专家",
                "system_prompt": "你是一个系统架构师，擅长高层设计、技术选型和架构决策。请考虑可扩展性、可维护性和最佳实践。",
            })
        
        # 通用/默认角色（如果没有匹配到专业角色）
        if not roles:
            roles.append({
                "name": "researcher",
                "icon": "🔍",
                "description": "综合研究和信息整合助手",
                "system_prompt": "你是一个研究助手，擅长信息收集、整理和综合。请提供全面、准确的信息，并标注关键发现。",
            })
            roles.append({
                "name": "critic",
                "icon": "🎯",
                "description": "质量评估和优化建议专家",
                "system_prompt": "你是一个质量评估专家，擅长发现潜在问题、提出改进建议和优化方案。请保持批判性思维，注重细节。",
            })
        
        # 根据复杂度决定子代理数量（最多3个）
        num_agents = min(len(roles), 2 + int(complexity * 2), 3)
        selected_roles = roles[:num_agents]
        
        # 构建子代理配置（复用主会话LLM，不单独配置模型）
        for i, role in enumerate(selected_roles):
            sub_agents.append({
                "id": f"auto-{role['name']}-{i}",
                "name": role["name"],
                "icon": role["icon"],
                "description": role["description"],
                "model_ref": "default",  # 复用主会话LLM配置
                "system_prompt": role["system_prompt"],
            })
        
        logger.info(
            "Auto-created %d sub-agents for task: %s",
            len(sub_agents),
            [a["name"] for a in sub_agents]
        )
        
        return sub_agents

    # ─────────── Cluster Parallel Execution ───────────

    async def _execute_cluster_parallel(
        self,
        user_input: str,
        sub_agents: list[dict],
        session_id: uuid.UUID,
    ) -> str | None:
        """
        真·并行集群执行
        
        使用 asyncio.gather 同时调用多个子代理，然后聚合结果
        """
        if len(sub_agents) < 2:
            return None
        
        logger.info(f"Starting parallel cluster execution with {len(sub_agents)} agents")
        
        # 推送进度：开始集群执行
        await self._emit_progress("cluster_start", f"启动 {len(sub_agents)} 个角色并行生成草稿...")
        
        try:
            from backend.agent.cluster_aggregator import (
                AggregationStrategy,
            )
            from backend.agent.cluster_executor import get_cluster_executor
            
            # 构建子任务
            sub_tasks = []
            for i, agent in enumerate(sub_agents):
                sub_tasks.append({
                    "id": f"agent-{i}",
                    "name": agent["name"],
                    "description": agent["description"],
                    "prompt": f"""用户请求：{user_input}

请根据你的专长给出回答。保持简洁，突出你的专业视角。""",
                    "agent_config": {
                        "agent_id": agent["id"],
                        "name": agent["name"],
                        "model_ref": agent["model_ref"],
                        "system_prompt": agent["system_prompt"],
                        "icon": agent["icon"],
                    },
                    "depends_on": [],
                    "metadata": {"original_index": i},
                })
            
            # 获取执行器
            executor = get_cluster_executor()
            
            # 定义进度回调（同步包装，兼容 executor 的调用方式）
            def progress_callback(task_id: str, progress: int, message: str):
                # 创建任务异步执行，避免阻塞 executor
                asyncio.create_task(self._emit_progress("cluster_progress", f"{message} ({progress}%)"))
            
            # 并行执行
            result = await executor.execute(
                task_description=user_input,
                sub_tasks=sub_tasks,
                aggregation_strategy=AggregationStrategy.SYNTHESIZE,
                progress_callback=progress_callback,
            )
            
            # 构建聚合结果
            if result.status.value == "completed":
                # 格式化各代理回复
                agent_responses = []
                for st in result.sub_tasks:
                    if st.status.value == "completed" and st.result:
                        agent_name = st.name
                        agent_icon = next((a["icon"] for a in sub_agents if a["name"] == agent_name), "🤖")
                        response_text = st.result.get("result", "") if isinstance(st.result, dict) else str(st.result)
                        agent_responses.append(f"{agent_icon} **{agent_name}**：{response_text}")
                
                # 添加聚合结果
                aggregated = result.aggregated_result
                if isinstance(aggregated, dict) and "synthesized" in aggregated:
                    final_text = f"""【多角色草稿汇总】

{chr(10).join(agent_responses)}

---

**综合结论**：
{aggregated['synthesized']}"""
                else:
                    final_text = f"""【多角色草稿汇总】

{chr(10).join(agent_responses)}"""
                
                # 推送完成事件
                await self._emit_progress("cluster_complete", "多角色草稿汇总完成")

                # 保存结果
                await self._persist_final_response(session_id, final_text)

                # 关键：cluster 路径在 run() 第 570 行提前 return，会跳过尾部统一的
                # idle 推送；若不在这里补推，前端气泡会一直停在「思考中」，
                # 直到用户手动停止才触发 idle 落盘。必须在 return 前显式恢复 idle。
                await self._push_status(session_id, "idle", "Ready")

                return final_text
            else:
                error_msg = f"多角色草稿执行失败: {result.error or '未知错误'}"
                await self._emit_progress("cluster_error", error_msg)
                # 失败路径同样会提前 return（见 run() 第 570 行），需补推状态避免前端卡「思考中」
                await self._push_status(session_id, "error", error_msg)
                return f"[多角色草稿] {error_msg}"
                
        except Exception as e:
            logger.error(f"Cluster parallel execution failed: {e}")
            await self._emit_progress("cluster_error", f"集群执行异常: {e}")
            return None  # 降级到单 LLM 模式
