# Takton 未来开发路线图

**版本基准**：0.5.0-alpha（Python 全栈）+ Rust Kernel/Runtime 0.1.0  
**文档版本**：2026-07-31  
**定位一句话**：本地优先、可审计、可治理的 **Personal Agent Runtime / 工作站**（Agent OS 方向），不是聊天套壳，也不是官方 Coding Agent 克隆。

---

## 0. 路线图怎么读

| 符号 | 含义 |
|------|------|
| ✅ | 已具备雏形 / 已落地 |
| 🚧 | 进行中 / 部分落地 |
| ⬜ | 未开始 |
| **P0** | 必做，拉开 OS 代差 |
| **P1** | 做完才有多 Agent 承载力 |
| **P2** | 平台化与生态，长期 |
| **N** | 明确不做或延后 |

**策略公理（写进决策红线）**

1. **Python 脑 + Rust 身**：规划 / LLM / RAG / 技能逻辑留 Python；进程 / 权限 / 预算 / 调度 / 资源 / 审计热路径在 Rust。  
2. **新开发 / 改旧控制平面代码：默认 Rust 替换 Python**（禁止在 Python 内核继续堆权威逻辑）。  
3. **禁止大迁移**：切片下沉、行为等价、再删旧实现。  
4. **功能做减法**：前端演示已够用；护城河在内核深度，不在功能列表。  
5. **先立 Agent Process Model**，不碰 Linux 内核本身。  
6. **差异化钉子**：本地 · 可审计 · 能力单调 · 资源可解释 · 工作站级治理。  

**0.5.0-alpha 当日交付摘要**：见 [`docs/RELEASE_0.5.0-alpha.md`](./RELEASE_0.5.0-alpha.md)。

---

## 1. 愿景与成熟度目标

### 1.1 目标态（约 1.0）

```
                    UI：Electron / Web（工作站壳）
                              │
                         API Gateway
                              │
              ┌───────────────┴───────────────┐
              │     Takton Core (Rust)        │
              │  Process · Cap · Mediate      │
              │  Scheduler · Resource (cgroup)│
              │  Audit chain · Event bus      │
              │  Isolation supervisor         │
              └───────────────┬───────────────┘
                              │ 系统调用 ABI
              ┌───────────────┴───────────────┐
              │   Python Agent Runtime（脑）   │
              │  Planner · LLM · Skills · RAG │
              │  Identity/Inbox 业务适配       │
              └───────────────────────────────┘
```

### 1.2 成熟度刻度（自评）

| 阶段 | 大概位置 | 标志 |
|------|----------|------|
| 0.4.x 现在 | **~40%** 下一代 Agent OS | 有 Kernel 思想；仍偏权限网关 + 应用编排 |
| 0.6（P0 完成） | **~55%** | Intent 强制闭环；真调度；默认隔离；多维资源 |
| 0.8（P1 完成） | **~70%** | IPC + 系统服务 + 可验证技能 + Eval 驱动 |
| 1.0（P2 核心） | **~85%+** | 稳定 ABI/SDK、可日用长程、可选 WASM 技能 |

> 第一份外部意见约 35–45%；路线图取中线 **~40%** 为起点，用可量化里程碑推进，不靠感觉。

---

## 2. 当前基线（As-Is）

### 2.1 已做对的

| 能力 | 状态 | 位置 |
|------|------|------|
| 统一 Run / AgentProcess | ✅ | Python + **Rust 进程表** |
| CapabilityToken 单调 narrowing | ✅ | 双端；签名 HMAC |
| `mediate` 强制中介 | ✅ 能力层 | Rust court + Python tool_hooks 完整层 |
| Token 预算 / soft renew / 审计哈希链 | ✅ | Rust ledger + JSONL |
| 权限法院 / 安全控制台 | 🚧 | Python 完整；Rust 仅 capability |
| 沙箱（bwrap / seatbelt / WSL / Job） | 🚧 | 可选，**非默认硬隔离** |
| Workforce 编制 / Inbox / Dispatcher | ✅ Python | 业务适配，非内核调度 |
| Evolution + 回放门禁 | 🚧 | 有引擎；验证门需加硬 |
| 记忆总线 / RAG | 🚧 | 有；缺系统服务化与主动整理 |
| Rust Kernel Host | 🚧 0.1 | `crates/takton-*` + `kernel_rust` 客户端 |
| 资源账户（多维） | 🚧 逻辑配额 | memory / concurrency / child_proc 等 |

### 2.2 关键缺口（三份意见交集）

1. Intent → 能力 → **工具 schema 强制闭环**  
2. 调度器未驱动真实执行（仍像任务台账）  
3. 资源只强管 token；CPU/内存/IO 硬限不足  
4. 沙箱非「每进程默认」  
5. 长程（小时～天）状态一致性与工具结果落盘  
6. 技能生成缺 **可测验证门**  
7. 无人机协作深度（可打断、可解释）不足  
8. 无固定 **Eval Harness**  
9. 无 Agent 间 **IPC** / 系统服务分层  
10. 无稳定第三方 **Agent SDK / ABI**

---

## 3. 目标架构（To-Be）

### 3.1 分层

| 层 | 技术 | 职责 | 原则 |
|----|------|------|------|
| **L0 Kernel** | Rust | 进程、能力、中介、预算、资源、调度、审计、隔离监督 | 热路径、可预测、崩溃隔离 |
| **L1 Runtime 服务** | Rust 优先 / Python 适配 | IPC、系统服务注册、上下文配额 | 经 syscall，不可绕过 |
| **L2 Agent 脑** | Python | Planner、LLM、工具实现、RAG、进化蒸馏 | 可热更、可试验 |
| **L3 业务面** | Python FastAPI | Auth、CRUD、会话、编制档案 | 不进内核热路径 |
| **L4 壳** | Next.js / Electron | 工作站 UI、权限控制台、Kernel 观测 | 少堆功能，服务治理 |

### 3.2 Agent Instance（调度单位）

未来一切调度对象统一为：

```text
Agent Instance {
  identity, goal, memory_handles,
  resource_quota, permissions / token,
  runtime (python|wasm), parent, state
}
```

Linux 的 process ≠ 容器 ≠ **Agent Instance**。Takton Kernel 只认后者。

### 3.3 系统调用 ABI（语义层，非改主机内核）

最小集（P0–P1 逐步落地）：

| 调用 | 语义 |
|------|------|
| `sys_proc_create/end/suspend/resume` | 进程生命周期 |
| `sys_cap_mediate` | 工具/技能/命令中介 |
| `sys_budget_charge / resource_charge` | 资源扣费 |
| `sys_fs_*` | 经能力与挂载根的文件 IO |
| `sys_exec` | 沙箱内命令 |
| `sys_net_request` | 网络（策略门控） |
| `sys_mem_read/write` | 记忆/上下文分页（P1） |
| `sys_ipc_send/recv` | Agent 间消息（P1） |
| `sys_event_emit` | 审计/领域事件 |

所有用户态 Agent **禁止**绕过 ABI 碰系统资源。

---

## 4. 版本与阶段路线

```text
0.4.x ──► 0.5 ──► 0.6 ──► 0.7 ──► 0.8 ──► 0.9 ──► 1.0
  今      契约     P0完    调度深  多Agent  打磨    日用级
         +切片    可用OS   +资源   承载力   +SDK   Runtime
```

| 版本 | 主题 | 周期（单人粗估） | 完成标志 |
|------|------|------------------|----------|
| **0.5** | 契约冻结 + Rust 默认生产 | 2–4 周 | ABI 文档 + 双端黄金测试；host 随安装包分发 |
| **0.6** | **P0：最小可用 AIOS Runtime** | +6–8 周 | Intent 闭环 + 真调度 + 默认隔离 + 多维资源 |
| **0.7** | 长程可靠 + 效率底座 | +4–6 周 | ✅ P0.5 已落地（checkpoint / 资源 / 长程观测） |
| **0.8** | **P1：多 Agent 与进化** | +8–10 周 | IPC + 系统服务 + 技能验证门 + Eval |
| **0.9** | 人机协作与 Coding Profile 打透 | +6–8 周 | 可打断/可解释；编程场景接近可用主力 |
| **1.0** | **Personal Agent Runtime GA** | +持续 | 稳定 ABI、日用文档、可选 WASM 技能 |

---

## 5. P0 — 夯实内核（0.5 → 0.6）

**目标**：权限网关 → **可调度、可配额、默认可隔离的 Runtime**。

### 5.1 工程清单

| ID | 工作项 | 产出 | 优先级 |
|----|--------|------|--------|
| K-01 | **Agent Process / Resource / Event ABI v1** 文档 + 契约测试 | `docs/kernel-abi-v1.md` + golden tests | P0 |
| K-02 | Rust host **默认随产品启动**；失败可观测降级 | 安装包含 `takton-kernel-host` | P0 |
| K-03 | **Intent → capabilities → 工具 schema 强制闭环** | 无 intent 不进显式能力进程；工具列表按 cap 裁剪 | P0 |
| K-04 | **Scheduler 驱动执行**（替换纯 session 锁） | 优先级 + aging；前台可优先 | P0 |
| K-05 | **Resource Manager 接线** | 并发槽 / 子进程数 / 工具结果字节硬拒；token 同步 | P0 |
| K-06 | **沙箱默认化** | profile：interactive / workforce / untrusted；默认非 local 裸跑 | P0 |
| K-07 | Isolation Supervisor 雏形 | spawn/kill/wait 与资源账户绑定；崩溃释放配额 | P0 |
| K-08 | Court 完整层策略迁入或编译进 Rust（path/secret 先） | mediate 热路径少跳 Python | P0 后半 |
| K-09 | 审计：决策轨迹可回放（工具+理由+预算快照） | Security Console / API 可还原一次 run | P0 |

### 5.2 产品清单

| ID | 工作项 | 说明 |
|----|--------|------|
| P-01 | 高危操作 **确认 → 执行 → 可回滚** 一等公民 | 文件写/命令：checkpoint 或 undo 入口 |
| P-02 | Kernel 观测页：资源账户 + 调度队列 + 链健康 | 5s 轮询已有进程/事件，补资源 |
| P-03 | **不做**新前台大功能（工作流编辑器大改等） | 做减法 |

### 5.3 P0 验收标准

- [ ] 任意工具路径必经 ABI mediate；绕过路径在代码审计清单为 0  
- [ ] 显式能力进程：未授权工具对模型 **不可见且不可调**  
- [ ] 双 Agent 抢资源：高优先级可先于低优先级获得 LLM/执行槽  
- [ ] 超并发 / 超 child_proc / 超逻辑内存：**拒绝并审计**，非静默拖死  
- [ ] workforce 任务默认沙箱（平台能力允许时）  
- [ ] `cargo test -p takton-kernel` + Python 契约测试 + smoke 全绿  

---

## 6. P0.5 / 0.7 — 长程可靠与效率

**目标**：能跑几小时不崩、不飘；成本可量化。

| ID | 工作项 | 说明 |
|----|--------|------|
| R-01 | Checkpoint + 工具结果落盘策略统一 | 大结果外置，上下文只留句柄 |
| R-02 | Doom loop / 迭代预算 / 预算耗尽 **优雅退出文案与恢复** | 已有雏形，打通 UX |
| R-03 | 上下文泄漏控制 | 子进程结束回收 cap 与资源；禁止能力残留 |
| R-04 | Provider Adapter + **缓存命中率仪表** | family 级断点/前缀；周报指标 |
| R-05 | Token / billable / 资源三维成本面板 | 编制日报已有 token，补资源 |

**验收**：固定「马拉松」任务（≥2h 模拟）恢复成功率、中断可解释率达标（自订阈值后写进 Eval）。

---

## 7. P1 — 多 Agent 承载力与进化（0.8）

**状态：✅ 已完成（2026-07-31）** — 见 [`RELEASE_0.5.0-alpha.md`](./RELEASE_0.5.0-alpha.md)

**目标**：多个 Agent 能在系统里协作、生存，而不是单任务 fan-out。

| ID | 工作项 | 说明 | 状态 |
|----|--------|------|------|
| M-01 | **内核 IPC**（点对点，经能力鉴权） | 未授权不可通信 | ✅ |
| M-02 | **系统服务**：Memory / Notify | 高特权常驻；用户 Agent 只 syscall | ✅ |
| M-03 | 记忆分层 + **主动整理** | working/episodic/semantic/skill | ✅ |
| M-04 | 上下文 **内核配额 + 换入换出** | `context_vm` | ✅ |
| M-05 | **技能验证门** | register→verify→activate→rollback | ✅ |
| M-06 | Evolution **永不自动改 live** | auto_apply 硬 false | ✅ |
| M-07 | **Eval Harness v1** | coding/research/long/safety | ✅ |
| M-08 | 最小 **Agent SDK** + 打包 | agent.json + pack 脚本 | ✅ |

**验收**：两个独立 Agent 经 IPC 协作；新技能未过验证门不可加载；Eval 可出分 — **均已通过联调**。

---

## 8. P2 — 平台化与 1.0（0.9 → 1.0）

**状态：✅ 已完成（2026-07-31）** — 见 [`RELEASE_0.5.0-alpha.md`](./RELEASE_0.5.0-alpha.md)

| ID | 工作项 | 说明 | 状态 |
|----|--------|------|------|
| E-01 | **Coding Profile 打透** | engineering / review / pair | ✅ |
| E-02 | 人机协作打断/改 plan/批准 | `collab_*` + suspend | ✅ |
| E-03 | **ABI 版本策略** | `abi_compat` 兼容窗口 | ✅ |
| E-04 | WASM Skill Runtime | fuel/memory 限额沙箱 | ✅ |
| E-05 | HAL 路径/命令/浏览器 | `hal_*` | ✅ |
| E-06 | 包管理 / 签名扫描 | `pkg_*` | ✅ |
| E-07 | 多设备 Instance 迁移 | export/import | ✅ |

---

## 9. 明确不做（N）与缓做

| 项 | 原因 |
|----|------|
| ❌ 全仓 Python → Rust | 一年无产品 |
| ❌ 改 Linux/Windows 内核 | 应做的是 Agent 层 OS |
| ❌ 对标 OpenClaw 堆多通道 | 稀释定位；通道可后置插件 |
| ❌ 正面硬刚 Claude Code/Codex 全场景 | 不同赛道；只打「治理下的编程能力」 |
| ❌ 无 Eval 的「智能自动改策略」 | 不可控 |
| ❌ 公有多租户 / SaaS 优先 | 本地单用户优先（治理红线） |
| ⏸ 重型前端新页面 | P0 冻结功能扩张 |

---

## 10. 对标与产品叙事

| 对象 | 关系 | 我们怎么打 |
|------|------|------------|
| **Claude Code / Codex** | 不同赛道 | 不拼纯编程极致；拼 **编程只是能力之一 + 可治理本地 Runtime** |
| **Hermes** | 最近竞品 | 差异化：**更强治理/安全控制平面 + 桌面工作站**，不拼社区学习循环速度 |
| **OpenClaw** | 轻量助手 | 不走「轻、多通道」；走 **重治理、可审计工作站** |

**对外口号建议**：  
*Governed, local-first Agent Runtime — 带 Kernel 的个人数字员工操作系统。*

---

## 11. 度量（用数据迭代）

| 指标 | 说明 | 阶段 |
|------|------|------|
| `mediate_deny_rate` / 可解释率 | 拒绝是否带 layer+rule | P0 |
| `budget_exhaust_graceful` | 耗尽是否优雅停 | P0 |
| `scheduler_wait_p95` | 排队公平性 | P0 |
| `sandbox_default_coverage` | 工具执行默认隔离比例 | P0 |
| `cache_hit_rate`（按 provider family） | 成本 | 0.7 |
| `marathon_resume_success` | 长程恢复 | 0.7 |
| `skill_ship_with_gate_rate` | 技能过门再上线 | P1 |
| `eval_suite_score` | 固定集总分趋势 | P1 |
| `abi_break_count` | 破坏兼容次数（目标 0 无迁移） | 全程 |

---

## 12. 单人执行节奏（务实）

### 每两周节奏

1. **1 个内核硬项**（K-xx）  
2. **1 个可演示验收**（控制台可见 / smoke）  
3. **0 个新大型前台功能**（P0 期间）  

### 精力分配建议（P0）

| 比例 | 投入 |
|------|------|
| ~70% | Rust Kernel / ABI / 调度 / 资源 / 隔离 |
| ~15% | Python 脑侧接线（Intent、loop、tool schema） |
| ~10% | 观测 / 文档 / Eval 骨架 |
| ~5% | 关键 bugfix |

### 文档优先

| 文档 | 状态 |
|------|------|
| `docs/ROADMAP.md` | 本文件 |
| `docs/KERNEL_RUST.md` | ✅ |
| `docs/kernel-abi-v1.md` | ✅ ABI 1.0.0 |
| `docs/agent-sdk.md` | ✅ 最小 SDK 说明 |
| `docs/TECHNICAL_MANUAL.md` | ✅ 技术手册 |
| `docs/RELEASE_0.5.0-alpha.md` | ✅ 0.5.0-alpha 交付摘要 |

---

## 13. 近期 90 天作战图（可直接排期）

```text
Week 1–2   ABI v1 + 契约测试；host 发布路径；Intent 接线设计
Week 3–4   Intent→工具裁剪强制；资源 charge 接入 tool/loop
Week 5–6   Scheduler 驱动 session/workforce 队列
Week 7–8   沙箱默认 profile + Isolation supervisor 雏形
Week 9–10  审计回放 + 控制台资源/队列；P0 验收清单打勾
Week 11–12 长程 checkpoint/结果落盘；缓存指标；开 Eval 骨架
```

**90 天出口（0.6 目标）**  
> 用户感知：Agent 永远带着「够用权限」干活；超限可解释；后台任务不饿死前台；危险动作默认在沙箱；Kernel 页能看懂资源与决策。  
> 工程感知：Python 脑可热更；Rust 身崩溃不拖垮 UI 进程；契约测试锁死语义。

---

## 14. 与代码树的映射

| 路线图模块 | 主要代码 |
|------------|----------|
| Rust Kernel | `crates/takton-kernel` |
| Runtime / Host | `crates/takton-runtime`, `crates/takton-kernel-host` |
| Python 适配 | `backend/kernel_rust/`, `backend/kernel/kernel.py::get_kernel` |
| Intent / 能力 | `backend/kernel/intent.py` → 迁强制到 create_process 路径 |
| Loop / 工具 | `backend/agent/loop*.py`, `loop_tools.py`, `tool_hooks.py` |
| 沙箱 | `backend/computer/*` + 未来 Isolation Supervisor |
| 编制 | `backend/kernel/dispatcher.py`, `inbox.py`, `identity.py`（P1 服务化） |
| 进化 | `backend/evolution/*`, `kernel/evolution_engine.py` |
| 观测 API | `backend/api/routes/kernel.py` |
| 桌面 | `frontend/`, `electron/` |

---

## 15. 一句话路线图

> **收窄场景、做硬内核、脑身分离、契约驱动、Eval 度量。**  
> 从「能 mediate 的 Agent 应用」走到「能调度、能配额、默认可隔离、可验证进化的 Personal Agent OS Runtime」——不靠堆功能，靠把 Kernel 做成真正的生命支持系统。

---

## 16. 修订记录

| 日期 | 变更 |
|------|------|
| 2026-07-31 | 初版：综合三份外部架构意见 + 现网 0.4.11 + Rust Kernel 0.1 落地 |
