# Agent-OS 标准下的 Takton 架构：现状 · 目标 · 前后端关系

> alpha 私有线 · 2026-07-29  
> 对齐业界 Agent-OS / Multi-Agent OS 分层（Kernel → Services → Runtime → Orchestration → User），  
> 映射 Takton 0.4.6-alpha + 0.5/0.6 预览，并说明「OS 化」对前后端拆分的真实要求。

---

## 0. 一句话

| 问题 | 答案 |
|------|------|
| 我们现在像不像 Agent-OS？ | **控制面已长出 Kernel 雏形**；整体仍是「厚后端 + 多页 SPA」的 **Personal Agent Workstation 向 OS 过渡态** |
| 目标长什么样？ | **Kernel 为唯一权威控制面**；FE 只是壳（桌面/Web/CLI 可多面）；主路径仍是员工/工单/审批 |
| 前后端分离要不要拆掉？ | **不要合并成单体**；要 **微调边界**：OS 化 = 强化后端控制面契约 + 前端变薄变多壳，不是取消分离 |

---

## 1. 业界 Agent-OS 标准分层（对照用）

综合 2025–2026 Agent-OS 蓝图 / Multi-Agent OS 叙事，可压成五层 + 横切：

```
┌─────────────────────────────────────────────────────────────┐
│  L5  User & Application Plane                               │
│      人机界面 · 审批 · 观测仪表 · 多客户端壳                   │
├─────────────────────────────────────────────────────────────┤
│  L4  Orchestration & Workflow                               │
│      多 agent 协作 · 工单/任务图 · 调度策略 · 组织关系         │
├─────────────────────────────────────────────────────────────┤
│  L3  Agent Runtime                                          │
│      推理循环 · 工具调用 · 上下文压缩 · 会话/续作              │
├─────────────────────────────────────────────────────────────┤
│  L2  Resource & Service Plane                               │
│      记忆 · 模型 · 工具/MCP · 文件/沙箱 · 通知 · RAG          │
├─────────────────────────────────────────────────────────────┤
│  L1  Kernel（控制面）                                        │
│      进程生命周期 · 能力令牌 · 中介裁决 · 预算 · 审计链       │
├─────────────────────────────────────────────────────────────┤
│  Cross-cutting: Security · Governance · Observability       │
└─────────────────────────────────────────────────────────────┘
```

**功能需求（Agent-OS 论文级）**：lifecycle · memory · tools · orchestration · observability · safety/governance · model/cost · interfaces ·（可选）multitenancy。  
**非功能**：reliability · durability · security-by-design · scalability · interoperability。

Takton 的产品裁剪：**单用户 · 数字班子 · 不做 0.6 前多租户 / HRT**。

---

## 2. 现状架构（按 Agent-OS 分层映射）

### 2.1 部署形态（物理）

```
┌──────────────── Electron / 浏览器 ────────────────┐
│  Next.js Frontend (:3000)                          │
│  REST + WebSocket → Backend                        │
└───────────────────────┬────────────────────────────┘
                        │ HTTP/WS /api
┌───────────────────────▼────────────────────────────┐
│  FastAPI Backend (:8090)                           │
│  routes · agent loop · kernel · services · DB      │
│  SQLite（权威）· 可选 Redis shared · 可选 Qdrant    │
└────────────────────────────────────────────────────┘
```

这是经典 **前后端分离 + 可选桌面壳**，不是「微内核进程内嵌 UI」。

### 2.2 逻辑映射：现状落在哪一层

| Agent-OS 层 | Takton 现状模块 | 成熟度 | 说明 |
|-------------|-----------------|--------|------|
| **L1 Kernel** | `backend/kernel/*`：process、capability、mediate、budget、events/hash、escalation、signing | **中高** | 已是控制面，但仍有 loop「搭桥」痕迹 |
| **L2 Services** | tools/MCP、RAG、memory、files、computer/sandbox、notifications、settings | **中高** | 执行与资源能力厚，是强项 |
| **L3 Runtime** | `NexusAgentLoop`、context_pipeline、skills、tool_policy、grant_store | **中高** | 会话/工单两条入口共用 loop 能力 |
| **L4 Orchestration** | Identity + Inbox + Dispatcher + workforce org/report；cluster/workflow **降级** | **中** | 编制调度像 OS；通用 workflow 不是主路径 |
| **L5 User** | Next 页：chat/agents/approvals/kernel/audit；Electron | **中** | 主路径在收；历史面仍多 |
| **Governance** | approval_rules、policy.decision、evolution 人批、protocol/governance 导出 | **中** | 红线可列举；未成独立策略引擎 |
| **Observability** | jobs/running、runs/recent、audit、kernel events | **中** | 够个人用；缺统一 Run ID 与 SLO |
| **Interop** | protocol 0.1（Agent Card、A2A-lite）、MCP | **低–中** | 本地互操作有了；非联邦 |

### 2.3 现状逻辑图（推荐心智）

```
                    ┌──────── L5 UI Shell ────────┐
                    │ Chat · 员工 · 审批 · 内核页  │
                    │ ProductConcepts: 三词心智     │
                    └────────────┬────────────────┘
                                 │ API / WS
         ┌───────────────────────▼───────────────────────┐
         │              API Gateway (FastAPI routes)      │
         └───────┬─────────────────┬─────────────┬───────┘
                 │                 │             │
    ┌────────────▼──────┐  ┌───────▼──────┐  ┌──▼────────────┐
    │ L4 Orchestration  │  │ L3 Runtime   │  │ L1 Kernel     │
    │ Identity Registry │  │ AgentLoop    │  │ Process/Cap   │
    │ Inbox + Dispatcher│  │ Context pipe │  │ Mediate/Budget│
    │ Evolution (人批)  │  │ Tools call   │  │ Escalation    │
    └────────┬──────────┘  └───────┬──────┘  └──┬────────────┘
             │                     │             │
             └──────────┬──────────┴─────────────┘
                        │ 一律应经 mediate / 预算 / 审计
             ┌──────────▼──────────┐
             │ L2 Services         │
             │ Tools·MCP·Memory    │
             │ Model·Sandbox·RAG   │
             └──────────┬──────────┘
                        │
             ┌──────────▼──────────┐
             │ Persistence         │
             │ SQLite · JSONL audit│
             └─────────────────────┘
```

### 2.4 现状诚实缺口（相对「像 OS」）

| 缺口 | 表现 | Agent-OS 含义 |
|------|------|----------------|
| **权威分裂** | 部分 chat 路径、旧 SubAgent/cluster 与 Inbox 编制并行 | 生命周期不唯一 |
| **UI 即产品面过厚** | 多路由 = 多概念；内核 API 已有，FE 仍堆历史页 | User 层淹没 Orchestration 叙事 |
| **Kernel 非唯一入口** | Loop 强，但「一切资源访问必经 kernel」未 100% 门禁 | 控制面不够 OS |
| **Run 模型碎** | chat run / process / inbox item / cron 语言不统一 | Observability 弱 |
| **壳与核耦合部署** | 桌面包常绑死同版本前后端；但协议边界已是 HTTP | 可多壳，尚未当一等公民 |

---

## 3. 目标架构（Agent-OS 对齐后的「我们该长成的样子」）

### 3.1 设计原则（产品裁剪后的 OS）

1. **Kernel 是唯一控制面**：进程、能力、预算、审计；无旁路提权。  
2. **编排只讲三词**：员工 / 工单 / 审批（Identity / Inbox / Escalation+Evolution）。  
3. **Runtime 可替换、可池化**：loop 是执行器，不是政策中心。  
4. **Services 可插拔**：MCP/工具/记忆后端换实现，不换合同。  
5. **User 层多壳单核**：Web / Electron / CLI / 外部 A2A 客户端共用同一 Kernel API。  
6. **Durable 默认路径**：工单与进程状态以 SQLite（+ 可选 Redis）跨重启。

### 3.2 目标分层图

```
┌─ L5  Shells（可多实例，无业务权威）─────────────────────────┐
│  Web SPA · Electron · CLI · 外部 Agent Card 消费者           │
│  只消费：sessions · jobs · approvals · protocol · streams    │
└────────────────────────────┬────────────────────────────────┘
                             │ 稳定契约 (OpenAPI + protocol 0.x + WS)
┌────────────────────────────▼────────────────────────────────┐
│  Facade / API 面（薄）                                        │
│  鉴权 · 单用户 · 错误人话化 · 不藏第二套策略                    │
└───┬──────────────┬─────────────────┬────────────────────────┘
    │              │                 │
┌───▼────┐   ┌─────▼──────┐   ┌──────▼───────┐
│ L4 编排 │   │ L3 运行时  │   │ L1 Kernel    │  ← 政策中心
│ 员工    │   │ Loop 池    │   │ 进程/令牌    │
│ 工单    │──►│ 上下文引擎 │──►│ mediate     │
│ 审批入口│   │ 工具执行桥 │   │ 预算/审计    │
└───┬────┘   └─────┬──────┘   └──────┬───────┘
    │              │                 │
    └──────────────┴────────┬────────┘
                            │ 仅经 kernel 授权后访问
                     ┌──────▼───────┐
                     │ L2 服务平面  │
                     │ 模型·工具·MCP│
                     │ 记忆·沙箱·RAG│
                     └──────┬───────┘
                     ┌──────▼───────┐
                     │ 状态平面     │
                     │ SQLite 权威  │
                     │ Audit JSONL  │
                     │ Redis 可选   │
                     └──────────────┘
```

### 3.3 目标组件清单（相对现状的「迁移动作」）

| 层 | 保持 | 加强 | 收敛/隐藏 |
|----|------|------|-----------|
| L1 | process/mediate/budget/hash | 统一 Run 关联 ID；所有危险 I/O 门禁 | 旁路工具直调 |
| L2 | tools/MCP/sandbox | 记忆写入唯一权威已文档化 → 执行期强制 | 第五套记忆 |
| L3 | loop + compact | 与 kernel gate 更深融合（已部分做） | 会话专用「第二权限」 |
| L4 | Identity/Inbox/Dispatcher | 崩溃恢复验收；协议投递一等公民 | Cluster/Workflow 作高级插件 |
| L5 | 主路径页 | 壳可替换；CLI 同等 API | 主轨再藏工程页 |
| 横切 | governance export | 策略预设一键应用；trace 抽样 | 静默 auto_apply |

### 3.4 版本化目标（与路线图对齐）

```
现在 0.4.6-alpha + 0.5/0.6 预览
  = L1 可用 · L4 编制可用 · L5 主路径半收束 · 协议 0.1

0.6.0 内测可用
  = Durable 盖章 · 三词心智硬 · 主路径 E2E 不脆

0.7+（示意）
  = 多壳一等公民 · Run 统一模型 · 协议 0.2（可选 A2A 增强）
  = 仍可不做多租户 SaaS
```

---

## 4. 前后端分离：OS 化要不要改架构？

### 4.1 结论（直接）

| 选项 | 建议 |
|------|------|
| 把前端逻辑搬进后端模板/SSR 大一统？ | **不必要**，且损害多壳 |
| 把 Kernel 写进 Electron 主进程、弃 FastAPI？ | **不推荐**（Python agent 栈已在后端） |
| 维持前后端分离，但 **重划契约**？ | **推荐**：微调，不是推倒 |

**OS 化的本质是「控制面与权威在 Kernel」**，不是「UI 与后端进程合并」。  
经典 OS 也是：用户态程序（多）↔ 系统调用/内核（一）。  
你们的 HTTP/WS ≈ 系统调用面；Next/Electron ≈ 用户态壳。

### 4.2 当前分离的问题（真问题 vs 假问题）

| 现象 | 是不是架构错？ | 说明 |
|------|----------------|------|
| 前后端两仓库/两进程 | **否** | 利于迭代、利于多壳 |
| 业务状态有的在 FE store | **部分是** | 应以服务端 jobs/session 为准；FE 只缓存 |
| 页面多 = 概念多 | **是产品/路由问题** | 用降级与三词约束，不必合并代码仓 |
| 长任务断线 | **边界问题** | 已有 stream restore / durable job；继续强化 **服务端权威** |
| Electron 打一层包 | **壳问题** | OS 化后 Electron 只是 L5 之一 |

### 4.3 推荐的「微调」边界（不必大拆）

```
┌──────────────────────────────────────────────────────────┐
│  现在：FE 偏「应用」，BE 偏「应用+内核」糊在一起的体感        │
└──────────────────────────────────────────────────────────┘
                           │ 微调
                           ▼
┌──────────────────────────────────────────────────────────┐
│  目标：                                                    │
│  · BE 显式拆成  Kernel API  |  Runtime  |  Services         │
│  · FE 只依赖「稳定契约」：jobs / identities / approvals /   │
│    protocol / streams —— 少直接碰内部表结构语义            │
│  · 任何新壳（CLI/托盘/外部脚本）只接同一契约                 │
└──────────────────────────────────────────────────────────┘
```

**建议做的微调：**

1. **契约分层（文档 + 路由前缀心智）**  
   - `/kernel/*` + `/kernel/protocol/*` = 系统调用 / 控制面  
   - `/sessions` `/chat` stream = 交互面  
   - 业务页不要再发明第四套状态机  

2. **状态权威 pure server**  
   - 在跑什么、工单状态、审批 pending：以 API 为准  
   - FE store 仅 UX（折叠、主题、草稿）  

3. **多壳准备（小改）**  
   - Electron 继续嵌 Web；CLI 调同一 REST  
   - 本地模式可保留「同机双端口」，不必强行 in-process UI  

4. **可选后续（0.7+）**  
   - `takton-kernel` 作为可独立进程/包，API 与 Web 解耦发版  
   - 静态 FE 完全 CDN/本地文件，后端只供 API（已接近）  

**不建议做的：**

- 为「像 OS」把 React 塞进 FastAPI 模板  
- 为「像 OS」上 Kubernetes/微服务拆十个服务（单用户场景过度）  
- 前端实现第二套权限/预算判断（必须问 Kernel）

### 4.4 前后端职责表（目标）

| 职责 | 后端（OS） | 前端（壳） |
|------|------------|------------|
| 谁能跑什么工具 | Kernel mediate + Identity caps | 只展示结果/拦截原因 |
| 工单生命周期 | Inbox + Dispatcher | 列表、停止按钮、派活表单 |
| 会话流式输出 | Loop + WS/SSE + snapshot | 渲染与恢复订阅 |
| 审批决策 | Escalation/Evolution API | 点通过/拒绝 |
| 进化是否应用 | 服务端强制人批 | 展示提案 |
| 主题/布局/i18n | — | FE |
| Agent Card 导出 | Protocol | 可选下载按钮 |

### 4.5 和「真正 OS」的类比

| 经典 OS | Takton 目标对应 |
|---------|-----------------|
| Kernel | `backend/kernel` + 门禁后的 services |
| Syscall | REST/WS + protocol 0.x |
| Userspace apps | Chat 壳、审批壳、CLI、外部自动化 |
| Process table | kernel processes + inbox claimed |
| Permissions | CapabilityToken + mediate |
| Init/systemd | Dispatcher + cron hooks |
| dmesg/auditd | events hash chain + `/audit` |

前后端分离 ≈ **userspace 与 kernel 分离**——这正是 OS 该有的，而不是缺陷。

---

## 5. 迁移路线（架构视角，非功能堆砌）

| 阶段 | 架构动作 | 产品可见 |
|------|----------|----------|
| **现在** | 维持单体后端仓库；协议 0.1；主路径心智条 | 已能讲清分层 |
| **0.6 盖章** | 强化 server-authoritative jobs；E2E/崩溃 | 「可离家」 |
| **OS 边界清晰化** | 文档+路由约定 Kernel API 为 syscall；FE 少碰旁路 | 多壳不改核 |
| **可选包拆分** | `kernel` 子包/进程独立版本号 | 研究/嵌入友好 |
| **不做** | 微服务爆炸、多租户控制面、HRT | 保持 Personal OS |

---

## 6. 总结

1. **按 Agent-OS 标准**：你们 **L1/L2/L3 骨架扎实，L4 编制路径正确，L5 在收束，治理/互操作刚补到可导出**；差在权威统一、Durable 实战、Run 统一与壳多样化。  
2. **目标架构**：单核多壳 · 三词编排 · Kernel 门禁一切危险能力 · 协议为本地 syscall 扩展。  
3. **前后端分离**：**保留**；OS 化要的是 **后端控制面更硬、契约更稳、前端更薄**，不是取消分离。  
4. **微调清单**：server-authoritative 状态、Kernel/Protocol 契约一等公民、多壳共用 API；大拆微服务或 SSR 合体 **性价比低**。

相关文档：

- `docs/internal/concepts.md` — 用户三词  
- `docs/internal/PROTOCOL.md` — 互操作 0.1  
- `docs/KERNEL_PLAN.md` — Kernel 演进  
- `docs/internal/ROADMAP_0.4.5_to_0.6.md` — 近程 0.4→0.6 产品目标  
- `docs/internal/ROADMAP_AIOS_OS_FULL.md` — **彻底 OS 化完整路线图**（Kernel-first · 事件化 · 多客户端）
- `docs/internal/ARCHITECTURE.md` · `TOPOLOGY.md` · `DEV_HANDBOOK.md` — 项目架构 / 拓扑 / 开发手册
