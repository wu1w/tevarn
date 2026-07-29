# Takton 彻底 OS 化完整路线图

> **定位**：alpha 私有线 · Personal Agent OS（数字班子）  
> **终局句**：从「带后台的桌面应用」演进为「拥有多个客户端的 Agent Runtime」  
> **不可替代的只有 Kernel**；Electron / Next.js / FastAPI 都是壳或适配层。  
> **原则**：不推倒技术栈；重新定义职责与启动模型；产品定义优先于工程炫技。

**关联文档**

| 文档 | 关系 |
|------|------|
| [README.md](./README.md) | 内部文档索引 |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | **项目架构**（分层·目录·模块） |
| [TOPOLOGY.md](./TOPOLOGY.md) | **系统拓扑**（进程·部署·数据·事件） |
| [DEV_HANDBOOK.md](./DEV_HANDBOOK.md) | **开发手册**（环境·约定·配方） |
| `ROADMAP_0.4.5_to_0.6.md` | **近程**：产品主路径与 Durable 内测 |
| `AGENT_OS_ARCHITECTURE.md` | 分层对照与前后端边界 |
| `PROTOCOL.md` / `concepts.md` | 互操作 0.1 与用户三词 |
| 本文 | **中远程**：OS 化阶段门 + 启动/事件/包结构 |

---

## 0. 战略结论（先定调）

### 0.1 采纳（与 GPT 建议对齐）

| 判断 | 决策 |
|------|------|
| Electron + Next + FastAPI 是否过时？ | **否**。过时的是「前端主角、后端服务者」的 **SaaS 假设** |
| 要不要换 Tauri / Rust / Go？ | **近 2 年默认不做**；无用户可感知收益 |
| 现在推倒重来？ | **否**；成本 ≫ 收益 |
| 现在就要布局的两件事 | ① Kernel **无 UI / 无 HTTP 依赖** ② 通信逐步 **Event 化** |
| 启动模型 | **Kernel 先活**；UI 只是后来连接的 Console（Linux ↔ SSH，不是浏览器 ↔ 站点） |
| 产品心智 | 仍只 **员工 / 工单 / 审批**；OS 术语不对主人弹 |

### 0.2 终局架构一句话

```
Kernel（纯 Runtime，可无界面常驻）
  └── Runtime Services（员工/工单/记忆/权限/调度/进化）
        └── Adapters（FastAPI · CLI · MCP · 未来 Remote）
              └── Clients（Electron Console · Web · CLI · 自动化）
```

用户关掉窗口 ≠ 系统关机；托盘里的「班子在干活」比聊天窗更 OS。

### 0.3 刻意不做（避免路线图膨胀）

- 公有多租户 SaaS、完整 K8s 控制面  
- 硬实时 HRT、为 OS 而换语言栈  
- 静默 `auto_apply` 改编制 caps  
- 把 React 塞进 Kernel 或把 Kernel 硬塞进 Electron 主进程业务逻辑  

---

## 1. 目标态：近似操作系统的职责模型

### 1.1 分层（逻辑，不是立刻拆仓）

```
┌─────────────────────────────────────────────────────────────┐
│  Clients（Userspace）                                         │
│  Electron Desktop Console · Web Dashboard · CLI · Scripts   │
│  角色：观察 · 审批 · 偶尔对话 · 通知展示                        │
├─────────────────────────────────────────────────────────────┤
│  Adapters（Syscall 面）                                       │
│  FastAPI HTTP/WS · CLI entry · MCP bridge · protocol 0.x    │
│  角色：鉴权、序列化、协议适配；**无业务权威**                    │
├─────────────────────────────────────────────────────────────┤
│  Runtime Services（子系统）                                   │
│  Identity · Inbox · Dispatcher · Memory · Evolution · Org   │
│  角色：编制与编排；可测、可事件化                                │
├─────────────────────────────────────────────────────────────┤
│  Kernel Core（控制面，纯 Python）                              │
│  Process · Capability · Mediate · Budget · EventBus · Audit │
│  角色：生命周期与策略门禁；**不 import FastAPI/Electron**       │
├─────────────────────────────────────────────────────────────┤
│  Resources                                                    │
│  SQLite 权威 · Audit JSONL · 可选 Redis · 模型/工具/沙箱      │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 启动模型（OS vs 今日常见）

| | 今日常见（SaaS 思维） | 目标（OS 思维） |
|--|----------------------|-----------------|
| 谁先起 | Electron → FE → 发现无 BE → 拉 BE | **Kernel/Runtime 服务先起**（或系统服务/托盘守护） |
| 关窗 | 常连带停后端 | **默认不杀 Kernel**；仅断客户端连接 |
| Agent 活着吗 | 依赖窗口 | 依赖 Kernel 进程与 durable 状态 |
| UI 角色 | 拥有系统 | **连接系统**（Console） |

### 1.3 通信模型

| 阶段 | 模式 | 说明 |
|------|------|------|
| 现在 | REST 为主 + 部分 WS | FE 主动问「现在有谁」 |
| 过渡 | REST 查询 + **领域事件推送** | 状态变更 Kernel 广播 |
| 目标 | **Event Stream 一等公民** | UI/CLI 订阅；REST 用于命令与快照 |

事件示例（产品语言）：

- `employee.status_changed`  
- `job.claimed` / `job.done` / `job.failed` / `job.cancelled`  
- `approval.pending` / `approval.resolved`  
- `process.ended` · `policy.decision` · `report.ready`  

### 1.4 技术栈角色重定义（不换栈）

| 组件 | 旧隐含角色 | 新角色 |
|------|------------|--------|
| **Kernel (Python)** | 后端里的一个模块 | **系统本体**；可独立进程常驻 |
| **FastAPI** | 业务中心 | **Kernel Adapter**（HTTP/WS 外壳） |
| **Next.js** | Web 应用主角 | **实时 Dashboard / Console UI** |
| **Electron** | 网页壳 | **Desktop Console**：托盘、通知、常驻、多窗、本地权限 UX |
| **SQLite** | 应用库 | **状态权威**（进程表/工单/编制） |

---

## 2. 与近程 0.4–0.6 的咬合

近程 **不要跳过** 产品与 Durable 盖章；OS 化大动作压在 0.6 之后分阶段做。

```
═══ 近程（已在进行 / 数周～数月）═══════════════════════════
0.4.6  Product Spine（三词主路径）
0.5.x  Durable Runtime（可离家、死信、停止、并发）
0.6.0  内测可用（7 天真实使用 + 无码 UX）

═══ 中程 OS 化（职责与启动）════════════════════════════════
0.7    Kernel-first 启动 · Adapter 边界 · 事件总线 MVP
0.8    Desktop Console（托盘=系统心跳）· UI Event 订阅深化
0.9    Kernel 无框架依赖硬化 · 多客户端同等公民（CLI）

═══ 远程近似 OS（可选扩展）════════════════════════════════
1.0    Agent Runtime 产品叙事成立 · 包边界清晰
1.x    可选：远程节点 / headless 主机 / 协议 0.2
       仍可不做多租户公有云
```

| 版本 | 一句话 | 用户可感知？ |
|------|--------|--------------|
| 0.6 | 班子能离家干活、权限可解释 | **强** |
| 0.7 | 关窗系统还在；事件开始推 | **中强** |
| 0.8 | 托盘=公司在上班；审批弹得准 | **强** |
| 0.9 | CLI/无界面也能管编制 | **中**（高手） |
| 1.0 | 「这是 Runtime，不是聊天 App」 | **叙事** |

---

## 3. 分阶段完整路线图

### Phase A — 0.6.0：产品 OS 前提（非架构大拆）

**目标**：验证「AI 公司」交互成立；Durable 可信。

| ID | 工作项 | DoD |
|----|--------|-----|
| A1 | 主路径 E2E：招人→派活→停跑→批权→日报 | 无码可走通 |
| A2 | kill -9 / 杀后端后工单终态或可续 | 对照 CRASH_RECOVERY |
| A3 | 7 天真实使用清单 | AIOS_OPERATOR 勾选 |
| A4 | 三词心智硬约束 | 高级页降级保持 |
| A5 | 协议 0.1 保持 | Card / A2A-lite / governance 可用 |

**明确不做**：换栈、拆微服务、大目录重组。

---

### Phase B — 0.7.0：Kernel-first 与 Adapter 边界（架构拐点）

**目标**：改 **启动模型 + 职责划分**，不换技术栈。

#### B1. 进程与启动

| 项 | 说明 |
|----|------|
| Kernel 宿主进程 | 独立可执行入口，例如 `python -m backend.runtime` 或 `takton-kernel`，**不依赖打开窗口** |
| Electron 策略 | 优先 **连接已有 Kernel**；没有再拉起；关主窗口 **默认不杀** Kernel（设置可「完全退出」） |
| 健康与单例 | 本地 port/lockfile；避免多 Kernel 抢库 |
| Windows/macOS | 可选登录启动「仅 Kernel」（后期） |

#### B2. 包/模块边界（逻辑层，可同仓 monorepo）

```
backend/
  kernel/           # 目标：零 FastAPI import
  runtime/          # Identity Inbox Dispatcher Memory Evolution 编排门面
  adapters/
    http/           # 现有 api/routes 迁入或薄包装
    cli/            # 新
  agent/            # Loop = Runtime 执行器（经 kernel 门禁）
  services/         # L2 资源
```

迁移策略：**绞杀者**——新代码按边界写；旧 routes 逐步只调 runtime/kernel，禁止再堆业务。

#### B3. Event Bus MVP

| 项 | 说明 |
|----|------|
| 进程内总线 | Kernel/Runtime 发领域事件（已有 event 链可扩展 kind） |
| WS 主题 | `GET/WS /events/stream` 或扩展现有 WS：客户端订阅 kinds |
| 快照 + 增量 | 连接时 REST 快照，之后只收事件（避免全量轮询） |
| 兼容 | 旧 REST 保留；新 UI 优先订事件 |

#### B4. FastAPI 角色

- 路由层：**命令**（enqueue、approve、stop）+ **查询快照** + **WS 适配**  
- 禁止在 route 内实现权限/调度核心逻辑（下沉 kernel/runtime）

**0.7 DoD**

- [ ] 无 UI 时 Kernel+Dispatcher 可单独跑 cron/工单  
- [ ] 关 Electron 主窗，托盘在且后端默认仍活  
- [ ] 至少 5 类领域事件可被 WS 订阅  
- [ ] `kernel/` 对 `fastapi` 的 import 数 → 0（adapters 除外）  

---

### Phase C — 0.8.0：Desktop Console 与实时仪表盘

**目标**：Electron/Next 按 OS Console 重定位。

#### C1. Electron = Desktop Console

| 能力 | 说明 |
|------|------|
| 系统托盘 | 主入口；显示在跑工单数 / 待审批角标 |
| 通知 | 工单完成/失败/待批（对接已有 notifications） |
| 多窗 | 审批快窗、聊天窗、可分离 |
| 退出语义 | 「退出控制台」vs「停止 AI 运行时」二选一明确 |
| 本地 UX | 权限申请、文件/设备与 OS 集成（既有可增强） |

#### C2. Next.js = Event Dashboard

| 从 | 到 |
|----|-----|
| 每页 REST 轮询拼状态 | 全局 Event store + 页面选择器 |
| Chat 为唯一主隐喻 | **驾驶舱/编制/审批** 与 Chat 并列；Chat=连接某员工 |
| 前端权威 store | 仅 UX 状态；业务状态以 Kernel 事件+快照为准 |

页面心智：

- 更像 **Linear / 飞书审批 / Grafana 子集**，而不是纯 ChatGPT  
- 仍保留对话；但不让对话「拥有」进程生命周期  

**0.8 DoD**

- [ ] 托盘角标与 `jobs/running`+pending 审批一致  
- [ ] 主路径三页以事件刷新为主，轮询仅兜底  
- [ ] 用户能理解：关窗 ≠ 停班子  

---

### Phase D — 0.9.0：多客户端同等公民

**目标**：Kernel 真有多个 userspace 程序。

| 客户端 | 能力下限 |
|--------|----------|
| CLI | `takton job list` / `job stop` / `approve` / `status` |
| Protocol | Agent Card、A2A-lite 稳定；脚本可派活 |
| Headless | 无 Electron 的「仅服务」安装模式（开发机/小主机） |
| Web | 可连本机 Kernel（已有）；远程仅可选、默认关 |

**0.9 DoD**

- [ ] 同一 Kernel 上 CLI 与 Electron 同时连接无状态撕裂  
- [ ] 文档：Client 开发指南（订阅事件 + 发命令）  

---

### Phase E — 1.0.0：Agent Runtime 叙事成立

**目标**：架构与产品话术一致，可称为 Personal Agent OS 内测版。

| 维度 | 标准 |
|------|------|
| 结构 | Kernel / Services / Adapters / Clients 目录与依赖单向 |
| 运行 | 默认 Kernel-first；Console 可插拔 |
| 产品 | 三词心智；OS 能力在高级/托盘/内核页 |
| 质量 | 崩溃恢复、事件不丢（至少 at-least-once + 幂等命令） |
| 互操作 | protocol ≥ 0.2（可选增强）；MCP 仍为工具面 |

**1.x 可选（非门禁）**

- 远程节点 / 多机 worker（共享状态已有 Redis 钩子）  
- 移动端只读审批  
- 换壳（若某日弃 Electron）→ 只重写 Client，不动 Kernel  

---

## 4. 工作流：从「目录按技术」到「目录按职责」

### 4.1 迁移原则（绞杀者，非 Big Bang）

1. **新功能**只加在正确层。  
2. **改旧功能**时下沉逻辑，route 变薄。  
3. **禁止** `kernel` → `api.routes` 反向依赖。  
4. **允许**同仓多年；逻辑边界先于物理拆包。  
5. 物理拆 `takton-kernel` 包放在 **0.9–1.0**，不在 0.7 强求。

### 4.2 依赖方向（硬规则）

```
Clients        →  Adapters only
Adapters       →  Runtime Services + Kernel
Runtime Svcs   →  Kernel + L2 services
Kernel         →  标准库 + 持久化抽象（不 → FastAPI/Next/Electron）
```

### 4.3 与现有代码落点

| 目标层 | 今日主要落点 | 迁移注意 |
|--------|--------------|----------|
| Kernel Core | `backend/kernel/*` | 去框架依赖；Event 出口统一 |
| Runtime Services | identity/inbox/dispatcher/workforce/evolution | 收成 runtime 门面 API |
| Agent 执行 | `backend/agent/*` | 保持；强制经 mediate |
| HTTP Adapter | `backend/api/*` + `main.py` lifespan | lifespan 启动的是 Runtime，不是「为 FE 服务」 |
| Desktop Client | `electron/*` | 启动顺序与退出语义 |
| Web Client | `frontend/*` | Event store；减业务权威 |

---

## 5. 事件化路线（通信）

### 5.1 阶段

| 步 | 内容 |
|----|------|
| E0（现有） | kernel events 环形缓冲 + JSONL；WS 偏 chat |
| E1（0.7） | 领域事件规范化（kind 稳定、payload schema） |
| E2（0.7–0.8） | 全局 `event_stream` WS；FE 订阅 |
| E3（0.8） | 驾驶舱/员工/审批无轮询或长间隔兜底 |
| E4（0.9） | CLI 也可 follow stream；断线重连 + since_ts/cursor |

### 5.2 命令 vs 查询 vs 事件

| 类型 | 载体 | 例 |
|------|------|-----|
| Command | REST/CLI | stop job、approve、enqueue |
| Query/Snapshot | REST | list identities、backup export |
| Event | WS/Bus | job.done、approval.pending |

UI 公式：**Snapshot on connect + Events after**。

---

## 6. 风险与反模式

| 反模式 | 为何有害 | 正确做法 |
|--------|----------|----------|
| 为 OS 而换 Tauri/Rust | 用户无感、拖死产品验证 | 换职责与启动 |
| 0.6 前大拆目录 | 打断 Durable/主路径 | 先 0.6 盖章 |
| UI 仍拥有生命周期 | Persistent Agent 假的 | Kernel-first + 关窗不杀 |
| 前端再实现权限 | 双源真相 | 只展示 mediate 结果 |
| Event 洪水无类型 | 难维护 | 固定 kind 目录与版本 |
| 微服务拆十个进程 | 单用户过重 | 逻辑分层、物理可单进程 |

---

## 7. 三年叙事（架构目标，不写「迁移到 XXX」）

| 年景 | 目标表述 |
|------|----------|
| **Y0（现在→0.6）** | 带 Kernel 的桌面 Agent 工作站；主路径与可离家成立 |
| **Y1（0.7–0.9）** | Kernel-first Runtime；Console 可插拔；事件驱动 UI；CLI 同等 |
| **Y2（1.0+）** | 名副其实的 Personal Agent OS：多客户端、可 headless、协议稳定 |
| **Y3** | 可选扩展远程/多节点；**仍可不做**公有多租户；换壳成本低 |

**唯一不可替换资产**：Kernel + 编制状态 + 审计链 + 事件语义。  
**可替换外壳**：Electron / Next / 甚至 FastAPI 实现（适配层可重写）。

---

## 8. 近期执行清单（从今天就能做，且不推倒）

按优先级，**不依赖换栈**：

| # | 动作 | 阶段 | 工作量感 |
|---|------|------|----------|
| 1 | 写清并遵守：关窗默认不杀后端（Electron 退出语义） | 0.7 可提前 | 小 |
| 2 | `python -m …` headless 启动文档 + 脚本（无 UI 跑 dispatcher） | 0.7 | 小 |
| 3 | 审计 `kernel` 对 web 框架的 import，列拆除清单 | 0.7 | 小 |
| 4 | 领域事件 kind 表（工单/审批/进程）与现有 emit 对齐 | 0.7 | 中 |
| 5 | WS 广播 MVP + FE 一页订阅试点（驾驶舱） | 0.7–0.8 | 中 |
| 6 | 托盘角标 = running + pending approval | 0.8 | 小 |
| 7 | CLI 最小三命令 | 0.9 | 中 |
| 8 | 完成 0.6 主人在场 DoD（并行最高优先） | 0.6 | 产品 |

---

## 9. 总览图

```
 产品验证                架构拐点                 Console 化              Runtime 叙事
    │                      │                        │                      │
 0.4.6 Spine          0.7 Kernel-first         0.8 Desktop Console     1.0 Agent OS
 0.5 Durable          · 关窗≠关机               · 托盘心跳               · 多客户端
 0.6 内测可用          · FastAPI=Adapter        · Event Dashboard        · 包边界清晰
    │                 · Event Bus MVP           · 减 REST 轮询              │
    └────────────────────┴────────────────────────┴──────────────────────┘
                         技术栈始终：Electron + Next + FastAPI（角色变，名字可不变）
```

---

## 10. 结语

GPT 建议的核心不是「重构技术」，而是：

1. **Kernel 成为主角**，UI 是观察者与管理终端；  
2. **启动像 OS**，不像 SaaS 站点；  
3. **通信像事件总线**，不像纯请求-响应后台；  
4. **FastAPI 降为适配层**，业务权威在纯 Python Runtime；  
5. **现在不推倒**，但 **边界与事件从 0.7 起硬化**。

Takton 近程仍用 `ROADMAP_0.4.5_to_0.6.md` 收 0.6；  
中远程以本文为 **OS 化单一事实来源（架构）**。  
二者冲突时：**产品主路径与 Durable 不让路；目录炫技让路。**
