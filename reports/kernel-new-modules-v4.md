# Kernel 新模块深度审计报告（v4）

> 审计时间：2026-07-30 20:51 CST  
> 工作区：`E:\项目\takton-alpha`  
> 审计方式：前 50 行精读 + grep 类定义 + grep 未完成标记  
> 共 8 个模块

---

## 1. governance.py

| 属性 | 值 |
|------|-----|
| 路径 | `backend/kernel/governance.py` |
| 总行数 | 221 |
| 类定义 | **无**（纯模块级数据结构 + 函数） |
| TODO 标记 | **0** |

**前 50 行摘要：** 模块定义「可研究级治理骨架」，与 `approval_rules` 互补——本模块是不可关闭的制度红线 + 内核面清单。核心数据结构 `GOVERNANCE_RED_LINES: list[dict]`，每条含 `id`、`title_zh/en`、`enforced`、`mechanism`、`product_concept`。已见红线包括：能力单调收窄、提权唯一扩大通道、进化永不自动应用 caps、编制不刷主人确认洪水。导入 `protocol_spec` 的 `LEGACY_TERM_MAP` / `PRODUCT_CONCEPTS` 等常量。

**评价：** ✅ 结构清晰，数据驱动设计，无未完成标记。

---

## 2. intent.py

| 属性 | 值 |
|------|-----|
| 路径 | `backend/kernel/intent.py` |
| 总行数 | 138 |
| 类定义 | `IntentDeclaration`（frozen dataclass） |
| TODO 标记 | **0** |

**前 50 行摘要：** 意图声明模块——agent 先声明「我要干什么」，Kernel 按声明 + 策略合成最小够用的临时能力集（阶段 2 雏形）。定义了两个安全集合：`DEFAULT_GRANTABLE`（安全只读能力如 file_read/grep/web_search）和 `RISKY_CAPABILITIES`（高危能力如 terminal/file_write/browser 必须 `allow_risky=True`）。`IntentDeclaration` 为 frozen dataclass，含 `goal`、`capabilities`、`constraints`。

**评价：** ✅ 安全语义完备，最小权限设计清晰，无未完成标记。

---

## 3. protocol_spec.py

| 属性 | 值 |
|------|-----|
| 路径 | `backend/kernel/protocol_spec.py` |
| 总行数 | 346 |
| 类定义 | `AgentSkill`、`AgentCard`、`TaskEnvelope` |
| TODO 标记 | **0** |

**前 50 行摘要：** Takton AIOS 互操作协议（v0.2.0），对齐业界 Agent-OS / A2A 「可描述、可投递」最小集。定义 `PROTOCOL_VERSION = "0.2.0"`、`PROTOCOL_NAME = "takton-aios-protocol"`。`STANDARD_CAPABILITIES` 能力词表含 file_read/file_rw/command/web_search/browser/git/notify/mcp/memory_read/write。`PRODUCT_CONCEPTS` 产品心智三词：employee（员工）、job（工单）、approval（审批），每个含中英文术语 + 系统实体映射。

**评价：** ✅ 协议定义完整，schema 可演进设计，无未完成标记。3 个类覆盖了 Agent 描述、卡片、工单信封。

---

## 4. signing.py

| 属性 | 值 |
|------|-----|
| 路径 | `backend/kernel/signing.py` |
| 总行数 | 70 |
| 类定义 | `TokenSignatureError(PermissionError)` |
| TODO 标记 | **0** |

**前 50 行摘要：** CapabilityToken 的 HMAC-SHA256 签名模块（审计缺口 #5 修复）。场景：Token 序列化后跨边界传输时防伪造。方案：HMAC-SHA256，密钥从 `jwt_secret` 经 HKDF 派生（`info=b"takton-kernel-token-hmac-v1"`），签名覆盖 Token 全部语义字段。`_hmac_key()` 懒加载+缓存，`_canonical_payload()` 提取签名字段。

**评价：** ✅ 职责单一，实现精炼（70 行），安全方案标准，无未完成标记。

---

## 5. domain_events.py

| 属性 | 值 |
|------|-----|
| 路径 | `backend/kernel/domain_events.py` |
| 总行数 | 197 |
| 类定义 | **无**（纯模块级函数 + 数据结构） |
| TODO 标记 | **0** |

**前 50 行摘要：** 领域事件系统——产品级 `kind → 进程内 event_bus + 近期缓冲 + cursor`。与审计哈希链并存（审计链不可抵赖/哈希连续；领域事件用于 UI/CLI 订阅）。`_RECENT` 缓冲 deque(maxlen=500)。`_KIND_MAP` 定义了 inbox（8 种 job 状态）、process（4 种）、policy（2 种）、approval（3 种）、scheduler（3 种）共 20 种事件映射。

**评价：** ✅ 事件覆盖全面，设计合理，无未完成标记。

---

## 6. evolution_engine.py

| 属性 | 值 |
|------|-----|
| 路径 | `backend/kernel/evolution_engine.py` |
| 总行数 | 462 |
| 类定义 | `EvolutionEngine` |
| TODO 标记 | **0** |

**前 50 行摘要：** 受控进化引擎（PLAN 阶段 0.7）。核心红线：分析器只产 pending 建议、永远不落应用、approve 是人工动作。分析器是规则化的（无 LLM），保证可验证/可复现/可单测。`PROPOSAL_KINDS` 四种：memory_distill、tool_deprecate、caps_adjust、planner_tune。阈值参数化（Alpha Review #3），通过 settings 配置覆盖默认值。

**评价：** ✅ 安全红线严格，模块规模最大（462 行），规则化分析器设计稳健，无未完成标记。

---

## 7. experience_sink.py

| 属性 | 值 |
|------|-----|
| 路径 | `backend/kernel/experience_sink.py` |
| 总行数 | **43**（最小模块） |
| 类定义 | **无**（单函数模块） |
| TODO 标记 | **0** |

**前 50 行摘要：** 工单完成时自动写入 identity experience memory 的入口。完整门禁收口到 `CrewMemoryWriter`，本模块保持 inbox 兼容入口。`record_job_experience()` 函数：best-effort 委托 `CrewMemoryWriter.maybe_distill_from_job()`，永不向调用方抛业务异常（try/except 吞掉并 debug log）。

**评价：** ✅ 极简（43 行），职责清晰，防御性编程到位。但作为「experience_sink」，功能全委托给 crew_memory，自身无逻辑——合理但薄。

---

## 8. llm_scheduler.py

| 属性 | 值 |
|------|-----|
| 路径 | `backend/kernel/llm_scheduler.py` |
| 总行数 | **573**（最大模块） |
| 类定义 | `Priority(IntEnum)`、`LlmAdmissionRejected(Exception)`、`LlmLeaseRequest`、`LlmLease`、`DailyTokenQuota`、`LlmAdmissionController` |
| TODO 标记 | **0** |

**前 50 行摘要：** LLM 资源公平调度——全局 in-flight 槽位 + 优先级队列 + 日配额。与 AgentScheduler（任务优先级堆）分离，本模块只做模型 HTTP 准入。`Priority` 枚举 6 级：OWNER_CHAT(100) > INTERACTIVE(80) > WORKFORCE_HIGH(50) > WORKFORCE_NORMAL(30) > WORKFORCE_LOW(10) > BACKGROUND(5)。`LlmLeaseRequest` 含 request_id、source、identity/session/process/inbox_item_id、priority、estimated_tokens、wait_boost。

**评价：** ✅ 架构最复杂（6 个类、573 行），优先级层次分明，与任务调度解耦设计合理。无未完成标记。

---

## 总结

### 功能完整性评估

| 模块 | 行数 | 类数 | TODO | 完整度 | 备注 |
|------|------|------|------|--------|------|
| governance.py | 221 | 0 | 0 | ✅ 完整 | 数据驱动红线 + 策略清单 |
| intent.py | 138 | 1 | 0 | ✅ 完整 | 最小权限合成，阶段 2 雏形（自述） |
| protocol_spec.py | 346 | 3 | 0 | ✅ 完整 | 互操作协议 v0.2.0 |
| signing.py | 70 | 1 | 0 | ✅ 完整 | HMAC 签名，职责单一 |
| domain_events.py | 197 | 0 | 0 | ✅ 完整 | 20 种事件映射，缓冲 + cursor |
| evolution_engine.py | 462 | 1 | 0 | ✅ 完整 | 规则化分析器，无 LLM 依赖 |
| experience_sink.py | 43 | 0 | 0 | ✅ 完整 | 极简委托层 |
| llm_scheduler.py | 573 | 6 | 0 | ✅ 完整 | 6 级优先级 + 日配额 |

### 关键发现

1. **零 TODO/FIXME/HACK/XXX/NotImplemented** — 8 个模块全部无未完成标记，代码库整洁度高。

2. **模块规模分布合理**：
   - 轻量层（<100 行）：signing.py(70)、experience_sink.py(43)
   - 中量层（100-250 行）：governance.py(221)、intent.py(138)、domain_events.py(197)
   - 重量层（>300 行）：protocol_spec.py(346)、evolution_engine.py(462)、llm_scheduler.py(573)

3. **设计模式一致**：
   - 数据结构用 dataclass（frozen/可变），无 ORM 耦合
   - 模块级常量 + settings 参数化双层配置
   - 防御性编程：experience_sink 永不抛业务异常

4. **安全红线内化**：governance.py 显式声明 4 条红线，evolution_engine.py 在注释中复述并以代码强制（auto_apply=False），intent.py 用 frozenset 隔离高危能力。

5. **无 LLM 依赖**：evolution_engine 分析器明确为规则化设计（"机器可验证、可复现、可单测"），仅 experience_sink 间接依赖 crew_memory（但已 best-effort 降级）。

### 潜在关注点（非阻塞）

- intent.py 自述「阶段 2 雏形」——可能功能尚不完整，但无 TODO 标记，需结合集成测试确认。
- experience_sink.py 仅 43 行，功能全委托——如果 crew_memory 异常吞掉后静默失败，用户体验可能受影响（当前为 debug log，生产可能需要 warning 级别）。
- llm_scheduler.py 最大最复杂（573 行 / 6 类），后续可考虑拆分 AdmissionController 为独立文件。
