# Takton Alpha 代码巡检总报告

> 执行人：小白（CEO）协调，四域工程师并行巡检
> 日期：2026-07-28
> 版本：0.4.6-alpha (Product Spine)
> 代码总量：~506 .py 文件，估算 ~30,000 行（后端）

---

## 一、模块概览

| 域 | 模块 | 文件数 | 健康度 | 关键风险 |
|---|---|---|---|---|
| 内核 | backend/kernel/ | 16 | 🟡 可用但有缺口 | 调度非 durable、权限三套并存 |
| 内核 | backend/core/ | 20 | 🟢 较好 | — |
| Agent | backend/agent/ | 53 | 🔴 loop.py 过重 | 2607 行上帝类 |
| Agent | backend/skills/ | 23 | 🟢 较好 | — |
| Agent | backend/tools/ | 20 | 🟢 较好 | — |
| Agent | backend/mcp_hub/ | 3 | 🟢 轻量可用 | — |
| 后端 | backend/services/ | 90 | 🟡 大文件多 | workflow_engine 1091行、channel_gateway 1025行 |
| 后端 | backend/api/ | 47 | 🟢 路由齐备 | 35+ 端点 |
| 后端 | backend/models/ | 34 | 🟢 模型完整 | 15+ 实体 |
| 后端 | backend/repositories/ | 32 | 🟢 模式统一 | UnitOfWork 抽象良好 |
| 后端 | backend/schemas/ | 28 | 🟢 较好 | — |
| 测试 | backend/tests/ | 94 | 🟡 覆盖不均 | 内核测试多，Agent 循环测试少 |
| 测试 | tests/ | 24 | 🟡 中等 | 合同测试+冒烟 |
| 测试 | e2e/ | 4 (2 spec) | 🔴 极薄 | 仅招人+派活，无执行/提权/日报 |

---

## 二、Kernel & Core 巡检报告

### 架构概览
内核层采用**五职能骨架**设计（kernel.py ~500行）：
- **Identity**（identity.py）：Agent 身份注册表，支持 CRUD + 能力授予
- **Scheduling**（dispatcher.py + scheduler.py）：进程内优先级队列 + 公平性调度
- **Capability**（capability.py）：能力令牌系统，带过期和签名
- **Permission**（approval_rules.py）：基于 settings KV 的审批规则
- **Audit**（core/audit.py）：审计日志

### 关键文件
| 文件 | 行数 | 职责 |
|---|---|---|
| kernel.py | ~500 | 控制平面骨架，串联五大职能 |
| process.py | ~300 | AgentProcess 执行实体 |
| dispatcher.py | ~500 | 休眠-唤醒-续作调度 |
| scheduler.py | ~200 | 优先级队列 + 公平性 |
| capability.py | ~170 | 能力令牌 |
| identity.py | ~500 | 身份注册表 |
| inbox.py | ~500 | 收件箱服务 |
| approval_rules.py | ~190 | 审批规则 |

### 路线图缺口对应

**G2（调度非 durable）** 🔴
- 现状：`dispatcher.py` 使用**进程内 asyncio 队列**，scheduler 是纯内存优先级堆
- 问题：进程重启后所有待执行任务丢失，cron 持久化依赖 DB 但 dispatcher 不持久化
- 建议：dispatcher 需要 WAL 或 outbox 模式，确保任务持久化到 DB

**G5（权限未一张网）** 🟡
- 现状：三套机制并存
  - `capability.py` — 能力令牌（CapabilityToken）
  - `approval_rules.py` — settings KV 审批规则
  - `identity.py` — 身份级别权限
- 问题：没有统一的权限检查入口，各模块各自判断
- 建议：统一到 capability 令牌 + approval rules 的组合判断

---

## 三、Agent Layer 巡检报告

### 架构概览
Agent 层是**最重量级**的模块（53文件），核心是 `NexusAgentLoop`（2607行）。

### 关键文件
| 文件 | 行数 | 职责 |
|---|---|---|
| loop.py | **2607** | 🔴 上帝类：Agent 主循环、消息组装、工具执行、子代理调度 |
| context.py | ~150 | 5 层上下文管理（memory/workspace/identity/session/rag） |
| context_engine.py | ~120 | 可插拔上下文引擎（Hermes 风格） |
| cluster_protocol.py | ~110 | JSON 任务分发协议 |
| subagent_runner.py | ~90 | 真子代理迷你 Run |
| tools/registry.py | ~110 | 统一工具注册表（5 来源） |
| mcp_hub/service.py | ~120 | MCP 协议中枢 |

### 路线图缺口对应

**G1（双轨心智）** 🟡
- 现状：Identity（identity.py）、SubAgent（subagent_runner.py）、Cluster（cluster_protocol.py）、Workflow（workflow_engine.py）四个概念并存
- 问题：没有统一的执行模型，每种模式有独立的状态机和上下文组装逻辑
- 建议：统一到 `AgentProcess` 抽象，各模式作为 Process 的不同启动策略

**G3（Run 不统一）** 🔴
- 现状：
  - Chat：`NexusAgentLoop.run()` 直接调用
  - Inbox：`inbox.py` → `dispatcher.py` → `scheduler.py` → `NexusAgentLoop`
  - Cron：`cron_scheduler.py` → `inbox.py`（复用 inbox）
  - Cluster：`cluster_protocol.py` 独立状态机
- 问题：四种入口，四种状态流转，没有统一的 Run 生命周期
- 建议：所有入口汇入 `AgentProcess.run()`，由 kernel 统一调度

---

## 四、Backend Services 巡检报告

### API 端点清单（35+ 端点）
核心路由注册在 `backend/api/routes/__init__.py`，主要端点：

| 类别 | 前缀 | 端点数 | 状态 |
|---|---|---|---|
| 认证 | /api/auth | 3 | ✅ 完整 |
| Agent Profile | /api/agent-profiles | 7 | ✅ CRUD+默认 |
| LLM Provider | /api/llm-providers | 6 | ✅ CRUD+默认 |
| Chat | /api/chat | 1 | ✅ SSE 流式 |
| Session | /api/sessions | 7 | ✅ CRUD+消息 |
| Knowledge | /api/knowledge | 6 | ✅ CRUD+RAG |
| Channel | /api/channels | 5 | ✅ CRUD+激活 |
| Message | /api/messages | 3 | ✅ CRUD |
| Task | /api/tasks | 5 | ✅ CRUD |
| Memory | /api/memory | 4 | ✅ 图谱+偏好 |
| Desktop | /api/desktop | 5 | ✅ 会话+工具 |
| Kernel | /api/kernel | 10+ | ✅ 身份/调度/能力 |

### 数据模型（15+ 实体）
| 实体 | 关系 | 备注 |
|---|---|---|
| AgentIdentity | → Capability, Memory | 核心身份表 |
| Session | → Message | 会话 |
| Message | → Session | 消息 |
| Task | — | 异步任务 |
| Knowledge / Chunk | 1:N | RAG 文档+分块 |
| MemoryNode / Edge | N:N | 知识图谱 |
| LLMProvider | — | 模型配置 |
| ChannelConfig | — | 消息通道 |
| UserProfile | — | 用户 |

### 路线图缺口对应

**G6（主路径 E2E 脆）** 🟡
- 招人 API（POST /api/kernel/identities）✅ 已有
- 派活 API（POST /api/kernel/inbox）✅ 已有
- 执行 API：依赖 `NexusAgentLoop.run()` ✅ 已有
- 提权 API：`escalate_to_human` 在 loop.py 中 ✅ 已有
- 日报 API：无独立端点 ❌ 缺失
- 建议：补日报聚合端点（GET /api/kernel/daily-report）

**G4（记忆未统一）** 🟡
- 现状：三层记忆并存
  - Episodic：`knowledge.py` + `chunk.py`（向量检索）
  - Identity：`agent_identity.py` 的 `initial_memory` 字段
  - Workspace：`ctx_items` 表 + `context.py` 5 层上下文
- 问题：三套存储，无统一检索接口
- 建议：统一到 `context_engine.py` 的可插拔引擎

---

## 五、质量工程巡检报告

### 测试概况
| 指标 | 值 |
|---|---|
| 测试文件总数 | 122 (94+24+4 E2E) |
| 框架 | pytest 8.3.5 + pytest-asyncio 0.24.0 |
| E2E 框架 | Playwright (TypeScript) |
| DB 策略 | sqlite+aiosqlite（临时文件，非 :memory:） |
| asyncio 模式 | auto（全异步） |

### E2E 覆盖
| 路径 | 覆盖 | 说明 |
|---|---|---|
| B1 招人 | ✅ | product-spine-hire-dispatch.spec.ts |
| B2 派活 | ✅ | 同上（inbox enqueue） |
| B3 执行 | ❌ | 无 LLM 调用测试 |
| B4 提权 | ❌ | 无 escalation 测试 |
| B5 日报 | ❌ | 无 daily report 测试 |

### 问题清单
1. 🔴 **E2E 极薄**：仅 2 个 spec 文件，主路径 5 步只覆盖 2 步
2. 🟡 **Agent 循环缺测试**：`loop.py`（2607行）几乎无直接单元测试
3. 🟡 **24 个 skip/TODO 标记**：分散在 kernel 测试中，多数是 stub 检查（确认 TODO 已清除）
4. 🟢 **conftest 设计合理**：临时文件 DB 避免了 :memory: 连接池问题

---

## 六、跨域风险清单

| 优先级 | 风险 | 影响域 | 路线图缺口 |
|---|---|---|---|
| 🔴 P0 | loop.py 2607 行上帝类 | Agent | G1, G3 |
| 🔴 P0 | 调度器非 durable | Kernel | G2 |
| 🔴 P0 | E2E 仅覆盖招人+派活 | QA | G8 |
| 🟡 P1 | 权限三套并存 | Kernel | G5 |
| 🟡 P1 | 记忆三层未统一 | Agent + Backend | G4 |
| 🟡 P1 | workflow_engine 1091 行 | Services | 可维护性 |
| 🟡 P1 | channel_gateway 1025 行 | Services | 可维护性 |
| 🟡 P2 | 日报 API 缺失 | Backend | G6 |
| 🟢 P3 | mcp_hub 仅 3 文件 | Agent | 扩展性 |

---

## 七、建议优先行动（对齐 0.4.6 Product Spine）

### 本周（W1）立即可做
1. **补 E2E 骨架**：在 `product-spine-hire-dispatch.spec.ts` 基础上扩展 B3（执行）、B4（提权）、B5（日报）的 mock-path 测试
2. **拆 loop.py**：将消息组装、工具执行、子代理调度拆为独立模块，降低单文件复杂度
3. **dispatcher 持久化**：将进程内队列改为 DB outbox 模式

### 下周（W2）
4. **统一权限入口**：合并 capability + approval + identity 为统一检查函数
5. **日报 API**：补 GET /api/kernel/daily-report 端点
6. **Agent 循环单元测试**：为 loop.py 的核心路径补 mock-based 单测

### 本月（W3-W4）
7. **记忆统一**：将三层记忆接入 context_engine 的可插拔引擎
8. **Run 统一**：所有入口汇入 AgentProcess.run()
9. **CI 集成**：pytest + playwright 纳入 CI pipeline
