# Takton 项目架构

> 对应路线图：`ROADMAP_AIOS_OS_FULL.md`  
> 拓扑图见：`TOPOLOGY.md`  
> 开发约定见：`DEV_HANDBOOK.md`

**终局句**：拥有多个客户端的 Agent Runtime；不可替代的只有 Kernel。

---

## 1. 架构原则

| # | 原则 | 含义 |
|---|------|------|
| P1 | **Kernel 主角** | 进程、能力、预算、审计权威在 Kernel；UI 不拥有生命周期 |
| P2 | **单核多壳** | Electron / Web / CLI / 脚本共用同一 Runtime |
| P3 | **三词产品面** | 用户只记 **员工 / 工单 / 审批**；其余高级或内部 |
| P4 | **依赖单向** | Clients → Adapters → Runtime Services → Kernel → Resources |
| P5 | **Server-authoritative** | 业务状态以服务端为准；FE store 仅 UX |
| P6 | **不换栈换职责** | 保留 Electron + Next + FastAPI；改角色与启动模型 |
| P7 | **绞杀者迁移** | 新代码按层写；旧代码顺手变薄；禁止 Big Bang 推倒 |

### 1.1 技术栈角色（重定义后）

| 组件 | 角色 | 不是 |
|------|------|------|
| Python Kernel + Runtime | 系统本体 / 控制面 + 编排 | 前端的附属后端 |
| FastAPI | **Adapter**（HTTP/WS 外壳） | 业务逻辑堆场 |
| Next.js | **Dashboard / Console UI** | 状态权威、策略引擎 |
| Electron | **Desktop Console**（托盘、通知、常驻） | 唯一生命周期主人 |
| SQLite | 状态权威存储 | 可有可无的缓存 |

---

## 2. 逻辑分层

### 2.1 目标分层（Agent-OS 对齐）

```
┌──────────────────────────────────────────────────────────────┐
│  L5  Clients（Userspace）                                      │
│      Electron Desktop Console · Web · CLI · 外部脚本           │
├──────────────────────────────────────────────────────────────┤
│  L4  Adapters（Syscall 面）                                    │
│      FastAPI routes · WebSocket · protocol · CLI · MCP 桥     │
├──────────────────────────────────────────────────────────────┤
│  L3  Runtime Services（编排子系统）                            │
│      Identity · Inbox · Dispatcher · Evolution · Workforce    │
├──────────────────────────────────────────────────────────────┤
│  L2  Agent Runtime（执行器）                                   │
│      NexusAgentLoop · context_pipeline · tools 调用桥         │
├──────────────────────────────────────────────────────────────┤
│  L1  Kernel Core（控制面）                                     │
│      Process · CapabilityToken · mediate · budget · audit    │
├──────────────────────────────────────────────────────────────┤
│  L0  Resources                                                 │
│      SQLite · Audit JSONL · 可选 Redis · LLM · 沙箱 · RAG    │
└──────────────────────────────────────────────────────────────┘
         横切：Governance · Observability · Security
```

### 2.2 层职责表

| 层 | 职责 | 允许依赖 | 禁止 |
|----|------|----------|------|
| **L5 Clients** | 观察、审批、对话渲染、托盘 | 仅 Adapter API | 实现权限/调度 |
| **L4 Adapters** | 鉴权、DTO、协议适配、推事件 | Runtime + Kernel 公共 API | 藏第二套策略 |
| **L3 Runtime Svcs** | 员工/工单/调度/进化编排 | Kernel + L0 services | import FastAPI |
| **L2 Agent Runtime** | 推理循环、工具执行 | Kernel.mediate、tools | 绕过 mediate 跑危险 I/O |
| **L1 Kernel** | 生命周期、令牌、预算、审计链 | 标准库 + 持久化抽象 | FastAPI / Electron / Next |
| **L0 Resources** | 存储、模型、沙箱、向量 | — | 反向依赖上层业务 |

### 2.3 产品概念 → 架构实体

| 用户说法 | 架构实体 | 主要模块 |
|----------|----------|----------|
| **员工** | AgentIdentity | `kernel/identity.py` |
| **工单** | AgentInboxItem | `kernel/inbox.py` + `dispatcher.py` |
| **审批** | Escalation + EvolutionProposal | `kernel/kernel.py` · evolution |
| 正在执行 | AgentProcess | `kernel/process.py` |
| 权限 | CapabilityToken + mediate | `kernel/capability.py` |
| 对话 | Session + Loop | `api/websocket` · `agent/loop.py` |
| 日报 | workforce report | `kernel/workforce.py` |

详见 `concepts.md`。

---

## 3. 仓库与目录架构

### 3.1 仓库顶层（现状 monorepo）

```
takton-alpha-…/
├── backend/                 # Runtime + Kernel + HTTP Adapter（同进程）
├── frontend/                # Next.js Console UI
├── electron/                # Desktop Console 壳（部分在 frontend/electron）
├── docs/internal/           # 本架构与路线图
├── e2e/ · tests/            # 端到端 / 集成
├── scripts/                 # 运维与补丁脚本
└── package.json · pyproject # 版本与依赖
```

### 3.2 Backend 目录（**现状 2026-07-29**）

| 路径 | 逻辑层 | 说明 |
|------|--------|------|
| `backend/kernel/` | **L1 + 编排实现体** | 控制面；无 FastAPI；Inbox/Dispatcher 实现仍在此 |
| `backend/runtime/` | **L3 门面 + Host 入口** | `facade.py` 导出编排 API；`python -m backend.runtime` |
| `backend/adapters/` | **L4 占位门面** | `adapters.http` → 委托 `backend.main` / `api.routes`（绞杀者） |
| `backend/agent/` | **L2** | Loop；直接 repo，不依赖 FastAPI dependencies |
| `backend/api/` | **L4 实际 HTTP** | routes 仍在此；经 adapters 可引用 |
| `backend/services/` · `tools/` · `models/` | L0 | 资源与持久化 |
| `backend/main.py` | Host + lifespan | 生产仍由此起 |

**依赖方向（已硬化）**

```
Clients → adapters.http / api  →  runtime.facade / kernel  →  repos/services
```

### 3.3 Frontend 目录

| 路径 | 职责 |
|------|------|
| `frontend/app/` | 路由页（驾驶舱、chat、agents、approvals、kernel、audit…） |
| `frontend/components/` | UI 组件；`layout/ProductConceptsBar` 三词条 |
| `frontend/lib/api.ts` | Adapter 客户端（REST） |
| `frontend/stores/` | **仅 UX 状态**（主题、locale、草稿）；业务以 API 为准 |
| `frontend/hooks/` | 数据钩子 |

**高级页**（Goals/Workflows/…）用 `LegacyQuiet` 降级，不占主轨。

### 3.4 Electron

| 职责（目标） | 说明 |
|--------------|------|
| 拉起或**连接** Kernel 宿主 | 优先复用已运行实例 |
| 托盘 / 通知 / 多窗 | Desktop Console |
| 退出语义 | 退出控制台 ≠ 停止 Runtime（0.7+） |
| 本地代理 | 开发/打包时反代 API |

---

## 4. 核心运行时架构

### 4.1 编制主路径（Product Spine）

```
主人 ──对话──► CEO/管家 Session (Loop)
                  │ crew_steward.hire / assign
                  ▼
            Identity Registry          Inbox
                  │                      │
                  │         Dispatcher.tick()
                  │                      │
                  └──────── claim ───────┘
                             │
                    create/use Process + Loop(workforce)
                             │
                         mediate(tools)
                             │
                      complete / fail / cancel
                             │
                      通知 · 日报 · credit
```

### 4.2 Kernel 控制面

| 能力 | 模块 | 说明 |
|------|------|------|
| 进程 | `process.py` / `kernel.py` | create → running → completed/failed/killed |
| 能力令牌 | `capability.py` | **只能 narrow**，扩大走 escalation |
| 中介 | `mediate` | 工具/意图放行或拒绝 |
| 预算 | `charge_tokens` + precheck | 防烧穿 |
| 审计 | events + `audit_store` JSONL | 哈希链 |
| 提权 | escalations | 人批 |
| 调度辅助 | `scheduler.py` | 进程内任务 |

### 4.3 工单状态机（简化）

```
pending ──claim──► claimed ──► done
                      │
                      ├── fail (attempts < max) ──► pending
                      ├── fail (max) ──► dead ──requeue──► pending
                      ├── cancel / stop ──► cancelled
                      └── timeout reclaim ──► pending
```

### 4.4 权限分层（产品）

| 谁 | 工具谁批 | 主人点吗 |
|----|----------|----------|
| 员工跑工单 | Identity.capabilities + steward | **否**（不刷屏） |
| 主人↔管家对话 | 危险可弹窗 | 可 |
| 扩权/进化 | 审批中心 | **是** |

---

## 5. 通信架构

### 5.1 现状

| 通道 | 用途 |
|------|------|
| REST `/api/*` | 命令 + 查询快照 |
| WebSocket | 对话流、部分实时 |
| Kernel events | 内存环 + JSONL；API 可读 |
| protocol 0.1 | Agent Card、A2A→工单 |

### 5.2 目标（Event-first Console）

```
Command  ──REST/CLI──►  Adapter ──► Runtime/Kernel
Query    ──REST──────►  Snapshot
Event    ◄──WS/Bus────  Kernel/Runtime 广播
```

UI 公式：**Connect 时 Snapshot + 之后只收 Events**。

领域事件（规范名，逐步对齐 emit）：

| Kind | 含义 |
|------|------|
| `job.enqueued` / `claimed` / `done` / `failed` / `cancelled` / `dead` | 工单 |
| `employee.created` / `suspended` / `status_changed` | 员工 |
| `approval.pending` / `resolved` | 审批 |
| `process.created` / `ended` | 进程 |
| `policy.decision` | 权限网 |
| `report.ready` | 日报相关 |

---

## 6. 数据与持久化

| 存储 | 角色 | 文档 |
|------|------|------|
| **SQLite** | 默认权威：编制、工单、会话、设置… | `STORAGE.md` |
| **Audit JSONL** | Kernel 事件链 | `kernel/audit_store.py` |
| **Redis** | 可选多 worker 热共享 | 默认关 |
| **Qdrant 等** | 可选向量 | RAG |

记忆写入权威：`MEMORY_AUTHORITY.md`（Identity memory 为主路径）。

---

## 7. 安全与治理架构

| 机制 | 位置 |
|------|------|
| Capability 单调收窄 | `capability.py` |
| 提权唯一扩权 | escalations |
| 进化人批 | EvolutionEngine；`auto_apply` caps 禁止 |
| 工单有界 | inbox max_pending |
| 策略预设 | `governance.py`：relaxed_visible / locked |
| 导出 | `GET /kernel/protocol/governance` |

---

## 8. 版本与双轨

| 线 | 定位 |
|----|------|
| **main 0.3.x** | 公开 Agent 终端；不强制 AIOS 叙事 |
| **alpha 0.4.x→OS** | 私有；Kernel + 编制；本架构文档只约束 alpha |

代码可 **main → alpha** cherry-pick；alpha 不反向污染 main 公开叙事。

---

## 9. 质量门（架构级）

| 门 | 检查 |
|----|------|
| 依赖 | `kernel` 不 import `fastapi` / `api.routes`（目标 0.7） |
| 主路径 | 招人→派活→停→批→日报可无码走通 |
| Durable | 杀后端后工单可解释（`CRASH_RECOVERY.md`） |
| 心智 | 主轨无新用户概念；高级页 LegacyQuiet |
| 测试 | `backend/tests/kernel` + 协议/治理单测 |

---

## 10. 相关文档

- `TOPOLOGY.md` — 进程/部署/数据/事件拓扑  
- `DEV_HANDBOOK.md` — 怎么改、改哪里  
- `ROADMAP_AIOS_OS_FULL.md` — 阶段门  
- `PROTOCOL.md` — 互操作  
- `../KERNEL_PLAN.md` — Kernel 演进细节  
