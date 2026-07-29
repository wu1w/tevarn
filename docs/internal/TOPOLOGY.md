# Takton 系统拓扑

> 配合 `ARCHITECTURE.md`（职责）与 `ROADMAP_AIOS_OS_FULL.md`（阶段）。  
> 图中「目标」指 0.7–1.0 OS 化方向；「现状」指 0.4.6-alpha 工作树。

---

## 1. 部署拓扑

### 1.1 现状（开发 / 桌面包）

```text
┌──────────────────── 开发者机器 / 用户 PC ────────────────────┐
│                                                              │
│  ┌─────────────┐     HTTP/WS      ┌──────────────────────┐  │
│  │  Browser 或  │ ◄─────────────► │  FastAPI :8090         │  │
│  │  Next :3000  │   /api · /ws    │  = Kernel+Runtime+API  │  │
│  └──────▲──────┘                  │  同进程                  │  │
│         │ 嵌入/加载                │  SQLite · JSONL         │  │
│  ┌──────┴──────┐                  └──────────▲─────────────┘  │
│  │  Electron   │  常 spawn uvicorn            │               │
│  │  (可选壳)   │ ─────────────────────────────┘               │
│  └─────────────┘                                              │
└──────────────────────────────────────────────────────────────┘
```

特点：

- **逻辑上**前后端分离；**物理上**桌面场景常由 Electron 拉起后端。  
- Kernel 与 HTTP **同进程**（尚未独立 `takton-kernel` 进程名）。

### 1.2 目标（Kernel-first · **已落地入口 0.7**）

```text
┌──────────────────────────── 用户机器 ───────────────────────────┐
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  Kernel Host（常驻）                                          │ │
│  │  python -m backend.runtime  |  scripts/start-kernel-host.ps1  │ │
│  │  · Kernel Core · Runtime (Inbox/Dispatcher) · SQLite         │ │
│  │  · HTTP Adapter :8090（同进程） · /runtime/status · /ws/domain│ │
│  └───────────────▲──────────────────▲──────────────────▲───────┘ │
│                  │ 连接              │                  │          │
│         ┌────────┴──────┐   ┌───────┴──────┐   ┌──────┴─────┐  │
│         │ Electron      │   │ Web :3000    │   │ CLI        │  │
│         │ Desktop Console│   │ Dashboard    │   │ takton …  │  │
│         │ 托盘·关窗≠停AI │   │ 事件流订阅   │   │ status/jobs│  │
│         └───────────────┘   └──────────────┘   └────────────┘  │
│                                                                   │
│  关 Console 窗口 ──默认──► 不断开 Kernel Host                      │
│  托盘「退出控制台」─────► 不杀 Host                                │
│  托盘「停止 AI 并退出」─► 停 Kernel Host                           │
└───────────────────────────────────────────────────────────────────┘
```

### 1.3 可选：多 worker（进阶，非默认）

```text
        ┌────────────┐
        │  Redis     │  可选：进程/提权热共享
        └─────▲──────┘
              │
     ┌────────┴────────┐
     │                 │
┌────┴────┐       ┌────┴────┐
│ Worker A│       │ Worker B│   各跑 FastAPI+Kernel 缓存
│ :8090   │       │ :8091   │
└─────────┘       └─────────┘
        ▲                 ▲
        └────────┬────────┘
                 │ SQLite 仍为权威（见 STORAGE.md）
```

单用户默认：**单进程 + SQLite**，不开 Redis。

---

## 2. 进程拓扑

### 2.1 现状进程树（桌面）

```text
electron.exe
 ├─ Chromium 渲染进程（Next UI）
 └─ (child) python -m uvicorn backend.main:app
              ├─ asyncio loop
              │   ├─ HTTP / WS handlers
              │   ├─ WorkforceDispatcher.run_forever
              │   ├─ cron / 其他 bg task
              │   └─ AgentLoop tasks（chat / 工单）
              └─ SQLite 连接
```

### 2.2 目标进程树

```text
takton-kernel-host          ← 先起，可无 UI
 ├─ Kernel + Dispatcher + Adapter
 └─ SQLite

takton-console (Electron)   ← 后连
 └─ UI only（不拥有 Kernel 生命周期）

takton CLI                  ← 可选并行连接
```

### 2.3 逻辑「进程」≠ OS 进程

| 名称 | 类型 | 说明 |
|------|------|------|
| **OS 进程** | uvicorn / electron | 机器上的进程 |
| **Kernel Process** | `AgentProcess` | Agent 一次运行的控制面对象 |
| **Inbox Job** | `AgentInboxItem` | 派给员工的工单 |
| **Session** | 对话会话 | 主人或员工 workforce 会话 |

关系：

```text
1 Job  ──执行时──►  0..1 Kernel Process
1 Chat turn ─────►  0..1 Kernel Process
1 Identity  ──────►  0..N Jobs · 0..1 busy（默认串行）
```

---

## 3. 控制面与数据面拓扑

```text
                    ┌──────────────┐
                    │   Clients    │
                    └──────┬───────┘
                           │ Command / Query / Subscribe
                    ┌──────▼───────┐
                    │  L4 Adapter  │  FastAPI · WS · protocol
                    └──────┬───────┘
             ┌─────────────┼─────────────┐
             │             │             │
      ┌──────▼──────┐ ┌────▼─────┐ ┌─────▼──────┐
      │ L3 Runtime  │ │ L2 Loop  │ │ 读模型/设置 │
      │ 员工·工单   │ │ 推理执行 │ │            │
      └──────┬──────┘ └────┬─────┘ └────────────┘
             │             │
             │    一律经 L1 mediate / budget
             │             │
      ┌──────▼─────────────▼──────┐
      │     L1 Kernel Core        │
      │  Process · Cap · Audit    │
      └────────────┬──────────────┘
                   │
      ┌────────────▼──────────────┐
      │  L0 Persistence           │
      │  SQLite · JSONL · (Redis) │
      └───────────────────────────┘
                   │
      ┌────────────▼──────────────┐
      │  外部世界                 │
      │  LLM API · MCP · 文件系统 · 沙箱 │
      └───────────────────────────┘
```

**红线**：危险工具 / 能力检查 **不得** 绕过 L1。

---

## 4. 产品主路径拓扑（用户可见）

```text
┌──────────┐   对话    ┌─────────────┐  hire/assign   ┌──────────┐
│  主人    │ ────────► │  CEO / 管家  │ ─────────────► │  员工    │
└──────────┘           │  Session    │                │ Identity │
     │                 └─────────────┘                └────┬─────┘
     │ 观察/审批                                            │
     ▼                                                      │ 工单
┌──────────┐  提权/进化  ┌─────────────┐              ┌─────▼─────┐
│  审批    │ ◄───────── │  Kernel /   │              │  Inbox    │
│  中心    │            │  Evolution  │              └─────┬─────┘
└──────────┘            └─────────────┘                    │
     │                                                     │ claim
     │                                            ┌────────▼────────┐
     │                                            │  Dispatcher     │
     │                                            │  + AgentLoop    │
     │                                            └────────┬────────┘
     │                                                     │
     └────────────── 日报 / 通知 / 内核页 ◄────────────────┘
```

用户三词闭环：**员工 · 工单 · 审批**。

---

## 5. 工单执行拓扑（序列）

```text
Client/API          Inbox           Dispatcher         Kernel           Loop
   │                  │                 │                │                │
   │  enqueue         │                 │                │                │
   │─────────────────►│ pending         │                │                │
   │                  │                 │  tick/claim    │                │
   │                  │◄────────────────│                │                │
   │                  │ claimed         │                │                │
   │                  │                 │  create/mark   │                │
   │                  │                 │───────────────►│ process        │
   │                  │                 │  run(workforce)│                │
   │                  │                 │────────────────────────────────►│
   │                  │                 │                │◄── mediate ────│
   │                  │                 │                │   tools/LLM    │
   │                  │ complete/fail   │◄───────────────────────────────│
   │                  │◄────────────────│  end_process   │                │
   │  notify/event    │                 │───────────────►│                │
   │◄─────────────────│                 │                │                │
```

停止路径：`POST /kernel/jobs/stop` → `cancel_job` → loop.stop + process killed + job cancelled。

---

## 6. 网络与端口拓扑

### 6.1 本机默认

| 端口 | 服务 | 说明 |
|------|------|------|
| **8090** | FastAPI / Kernel Adapter | **统一默认**（CLI / Electron / 手册 / e2e） |
| 8000… | 候选 | Electron 仅用于发现历史/孤儿 Host |
| **3000** | Next dev | 开发；生产可静态托管或 Electron 加载 |
| 可选 | Redis | 仅 multi-worker |

环境示例：

```text
TAKTON_AIOS_PROFILE=aios-dev
# SQLite 路径由配置/数据目录决定
NEXT_PUBLIC_API_URL=/api   # 开发时代理到 8090
```

### 6.2 流量类型

```text
Browser/Electron
    │
    ├─ REST  /api/kernel/*     命令与快照（编制、工单、审批、协议）
    ├─ REST  /api/sessions/*   会话
    ├─ WS    /api/ws…          对话流（现状主通道）
    └─ WS    /events/stream    领域事件（目标 0.7+）
```

---

## 7. 事件拓扑（目标 0.7+）

### 7.1 总线位置

```text
  Identity / Inbox / Kernel.mediate / Evolution
              │
              ▼  emit(domain_event)
       ┌──────────────┐
       │  Event Bus   │  （进程内 + 可落 JSONL）
       └──────┬───────┘
              │
       ┌──────┼──────────────┐
       ▼      ▼              ▼
   WS 适配  CLI follow   Audit 存储
       │
       ▼
   Console UI store（只缓存，不权威）
```

### 7.2 事件 vs 命令

| 方向 | 例 |
|------|-----|
| **In（Command）** | enqueue job、approve、stop job、hire |
| **Out（Event）** | job.done、approval.pending、process.ended |
| **In（Query）** | list employees、backup export、agent-cards |

### 7.3 与现有 kernel events 关系

现状：`KernelEvent` 环形缓冲 + 哈希链（偏控制面审计）。  
目标：在其上（或旁路规范化）增加 **产品领域 kind**，供 UI 订阅；审计链不削弱。

---

## 8. 存储拓扑

```text
┌─────────────────────────────────────────┐
│  SQLite（权威）                           │
│  · users / sessions / messages          │
│  · agent_identities / inbox_items       │
│  · kernel_processes / escalations 档案  │
│  · settings / notifications / …         │
└──────────────────▲──────────────────────┘
                   │ repositories
┌──────────────────┴──────────────────────┐
│  Runtime / Kernel                         │
└──────────────────┬──────────────────────┘
                   │ append
┌──────────────────▼──────────────────────┐
│  ~/.takton/kernel_events.jsonl（示例）    │
│  哈希链审计                               │
└─────────────────────────────────────────┘
                   │ 可选
┌──────────────────▼──────────────────────┐
│  Redis shared_store（多 worker 热状态）   │
└─────────────────────────────────────────┘
                   │ 可选
┌──────────────────▼──────────────────────┐
│  Vector DB（RAG / identity memory 索引）  │
└─────────────────────────────────────────┘
```

---

## 9. 客户端拓扑（多壳）

```text
                    ┌──────────── Kernel Host ────────────┐
                    │         Adapter :8090                 │
                    └───────▲─────────▲─────────▲─────────┘
                            │         │         │
              ┌─────────────┘         │         └─────────────┐
              │                       │                       │
     ┌────────┴────────┐    ┌─────────┴────────┐    ┌─────────┴────────┐
     │ Desktop Console │    │ Web Dashboard    │    │ CLI / A2A script │
     │ Electron        │    │ Next.js          │    │ protocol 0.1     │
     │ · 托盘          │    │ · 驾驶舱         │    │ · seed / stop    │
     │ · 通知          │    │ · 员工/审批      │    │ · agent-cards    │
     │ · 多窗          │    │ · 内核协议页     │    └──────────────────┘
     └─────────────────┘    └──────────────────┘
```

所有客户端 **地位同等**：谁都不拥有 Kernel，只连接。

---

## 10. 信任与权限边界拓扑

```text
外网 LLM / MCP
       ▲
       │ 出站（策略/网络安全）
┌──────┴──────────────────────────────┐
│  Tool 执行层                          │
│    ▲ 仅当 mediate allow               │
│  Loop / Dispatcher                    │
│    ▲                                  │
│  Kernel CapabilityToken               │
│    ▲ narrow only                      │
│  Identity.capabilities（编制）         │
│    ▲ 扩权仅 escalation 人批            │
│  主人（审批中心）                       │
└───────────────────────────────────────┘
```

---

## 11. 拓扑演进检查表

| 阶段 | 拓扑变化 |
|------|----------|
| **现在** | 单机；Electron 常拉起后端；REST 为主 |
| **0.7** | Kernel-first 启动文档/脚本；关窗不杀；事件 WS MVP |
| **0.8** | 托盘为系统心跳；UI 订事件 |
| **0.9** | CLI 并行连接；headless 模式一等 |
| **1.0** | 拓扑与叙事一致：Runtime + 多 Console |

---

## 12. 相关文档

- `ARCHITECTURE.md` — 层与目录职责  
- `DEV_HANDBOOK.md` — 开发时如何不破坏拓扑  
- `STORAGE.md` · `CRASH_RECOVERY.md` · `PROTOCOL.md`  
