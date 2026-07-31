# Takton 路线图 · 0.5.0-alpha 之后

**版本基准**：`0.5.0-alpha`（`backend/VERSION`）  
**分支**：[`feature/agent-kernel`](https://github.com/wu1w/takton/tree/feature/agent-kernel)  
**文档版本**：2026-07-31（P0 里程碑收口；**产品版本号维持 0.5.0-alpha**）  
**定位**：本地优先、可审计、可治理的 **Personal Agent Runtime / 工作站**（Agent OS 方向）。  
不是聊天套壳，也不是官方 Coding Agent 克隆。

**关联文档**：[RELEASE_0.5.0-alpha](./RELEASE_0.5.0-alpha.md) · [P0 交付说明](./RELEASE_0.6.0-alpha.md) · [KERNEL_RUST](./KERNEL_RUST.md) · [kernel-abi-v1](./kernel-abi-v1.md) · [agent-sdk](./agent-sdk.md) · [TECHNICAL_MANUAL](./TECHNICAL_MANUAL.md)

---

## 0. 怎么读这份路线图

| 符号 | 含义 |
|------|------|
| ✅ | 已落地且可作为默认依赖（或契约已冻结） |
| 🧩 | **切片已进仓库**（有实现 / smoke / 文档），默认路径或产品化未打透 |
| 🚧 | 进行中 / 部分可用 |
| ⬜ | 未开始 |
| **H** | Hardening 打磨（本阶段主线） |
| **N** | 明确不做或延后 |

### 策略公理（决策红线）

1. **Python 脑 + Rust 身**：规划 / LLM / RAG / 技能逻辑留 Python；进程 / 权限 / 预算 / 调度 / 资源 / 审计热路径在 Rust。  
2. **改控制平面：默认强化 Rust 权威**；禁止在 Python 内核继续堆新的权威逻辑。  
3. **禁止大迁移**：切片下沉 → 行为等价 → 再删旧实现。  
4. **功能做减法**：护城河在内核深度与默认可依赖，不在功能列表长度。  
5. **先立 Agent Process Model**，不碰 Linux/Windows 内核本身。  
6. **差异化钉子**：本地 · 可审计 · 能力单调 · 资源可解释 · 工作站级治理。  
7. **切片 ≠ 验收**：0.5 已有大量 P1/P2 能力切片；**下一阶段以「默认路径打磨」为主**，不急着再开大功能面。

### 对外口号

> **Governed, local-first Agent Runtime — 带 Kernel 的个人数字员工操作系统。**

---

## 1. 进度快照（As-Of 0.5.0-alpha）

### 1.1 成熟度刻度

| 阶段 | AIOS 完成度（自评） | 标志 |
|------|-------------------|------|
| 0.4.x | ~40% | Kernel 思想；偏权限网关 + 应用编排 |
| 0.5 + Phase H | ~48% | 双端控制平面 + ABI + H 打磨 |
| **0.5.0-alpha + P0（现在）** | **~55%** | 产品版本仍为 **0.5.0-alpha**；路线图 P0/「0.6」验收：Intent 闭环 · run_gate · 默认隔离 · 资源硬拒+审计 |
| 0.7（路线图） | ~62% | 长程可靠 + 成本可观测（**不自动升产品号**） |
| 0.8 | ~70% | 多 Agent 协作产品化 + Eval 驱动演进 |
| 0.9 | ~78% | 人机协作与 Coding Profile 打透 |
| 1.0 | ~85%+ | 稳定 ABI/SDK、日用文档、可选 WASM 技能常态 |

> **产品版本号**与**路线图阶段名**分离：当前对外版本固定 **0.5.0-alpha**，直至明确发布决策再升号。  
> 下一路线图阶段：**0.7 长程/成本**（§6）。

### 1.2 分层成熟度

```text
L4 壳（Web / Electron）           ████████░░  ~80%  工作站壳够用
L3 业务 API（会话 / 编制 / 市场）  ███████░░░  ~70%  可用；生产装包需加固
L2 Agent 脑（LLM / 工具 / RAG）   ███████░░░  ~70%  强编排；Provider 计量已做
L1 Runtime 服务（IPC / 记忆服务）  █████░░░░░  ~50%  切片有；默认协作路径浅
L0 Kernel（进程 / cap / 调度 / 隔离）████░░░░░░  ~45%  双端有；默认强制未打穿  ← 主战场
```

### 1.3 版本时间线（已交付）

| 版本 | 主题 | 对 AIOS 的贡献 |
|------|------|----------------|
| ≤0.4.9 | Run / Court / Crew / 多后端沙箱 | 应用级治理 Runtime |
| 0.4.10 | Phase 对齐、弹性预算 soft_renew、公开文档瘦身 | 长任务可跑、仓库可开源 |
| 0.4.11 | 多 Provider Profile / cache / billable | 成本与配额侧（计费意识） |
| **0.5.0-alpha** | Rust host、Phase H、P0 验收（cap_tools / resource_denied / tools 门控） | 控制平面 + **最小 AIOS Runtime 路径**（版本号仍 0.5.0-alpha） |
| → 路线图 0.7+ | 长程 / 成本 / 多 Agent 产品化 | 仍可维持 0.5.x 产品号直至 GA 决策 |
| → 1.0 | ABI 稳定 + 日用 | Personal Agent Runtime GA |

### 1.4 能力矩阵（切片 vs 默认可依赖）

| 能力 | 状态 | 说明 |
|------|------|------|
| 统一 Run / AgentProcess | ✅ / 🧩 | Python + Rust 进程表；双端需持续对齐 |
| CapabilityToken 单调 narrowing | ✅ | 双端；HMAC 签名 |
| `mediate` / tool_gate | ✅ 能力层 | 热路径仍可能回落 Python 完整 court |
| Token 预算 / soft renew / 审计链 | ✅ | billable 优先；硬顶可配 |
| 权限法院 / 安全控制台 | 🚧 | Python 完整；Rust 偏 capability |
| 沙箱（bwrap / seatbelt / WSL / Job） | 🚧 | 默认倾向 sandbox；无能力环境需降级 |
| Workforce / Inbox / Dispatcher | ✅ 业务面 | 非内核调度权威 |
| Evolution + 回放门禁 | 🚧 | 不自动改 live；验证门继续加硬 |
| 记忆总线 / RAG | 🚧 | 有；系统服务化与主动整理偏切片 |
| Rust Kernel Host | 🧩 0.1 | 可跑；发布物与 ABI 对齐要打磨 |
| 多维资源账户 | 🧩 | 逻辑配额有；硬拒与 loop 接线要打磨 |
| Intent → 工具裁剪闭环 | 🚧 | 接线有；强制「模型不可见」未验收 |
| Scheduler 驱动真实执行 | 🚧 | API/结构有；替代 session 锁未验收 |
| Provider 缓存 / billable | ✅ | family Profile；周报仪表仍浅 |
| IPC / 系统服务 / skill_gate / Eval / SDK / WASM | 🧩 | 0.5 大量切片；产品默认路径浅 |
| 包市场信任根 / 签名 | 🧩 | 有；生产密钥与白名单必配 |

**0.5 交付详情**：见 [RELEASE_0.5.0-alpha.md](./RELEASE_0.5.0-alpha.md)。

---

## 2. 目标架构（To-Be · 不变）

```text
                 UI：Electron / Web（工作站壳）
                           │
                      API Gateway
                           │
           ┌───────────────┴───────────────┐
           │     Takton Core (Rust)        │
           │  Process · Cap · Mediate      │
           │  Scheduler · Resource         │
           │  Audit chain · Event bus      │
           │  Isolation supervisor         │
           └───────────────┬───────────────┘
                           │ 系统调用 ABI
           ┌───────────────┴───────────────┐
           │   Python Agent Runtime（脑）   │
           │  Planner · LLM · Skills · RAG │
           │  Identity / Inbox 业务适配     │
           └───────────────────────────────┘
```

| 层 | 技术 | 职责 |
|----|------|------|
| **L0 Kernel** | Rust | 进程、能力、中介、预算、资源、调度、审计、隔离监督 |
| **L1 Runtime 服务** | Rust 优先 | IPC、系统服务、上下文配额（经 syscall） |
| **L2 Agent 脑** | Python | Planner、LLM、工具实现、RAG、进化蒸馏 |
| **L3 业务面** | FastAPI | Auth、CRUD、会话、编制档案 |
| **L4 壳** | Next.js / Electron | 工作站 UI、权限控制台、Kernel 观测 |

**调度单位**：`Agent Instance`（identity · goal · memory · quota · permissions · runtime · parent · state）— 不是容器，也不是裸 OS 进程。

**ABI 语义**（见 [kernel-abi-v1.md](./kernel-abi-v1.md)）：`sys_proc_*` · `sys_cap_mediate` · `sys_budget/resource_charge` · `sys_fs_*` · `sys_exec` · `sys_net_*` · `sys_mem_*` · `sys_ipc_*` · `sys_event_emit`。  
用户态 Agent **禁止**绕过 ABI 碰系统资源。

---

## 3. 主线版本计划（0.5 → 1.0）

```text
0.5.0-alpha ──► 0.5.x ──► 0.6 ──► 0.7 ──► 0.8 ──► 0.9 ──► 1.0
   现在         打磨切片    最小AIOS  长程/成本  多Agent   协作/编程  日用GA
              (H 系列)     Runtime   可靠      产品化    打透
```

| 版本 | 主题 | 周期（单人粗估） | 完成标志 |
|------|------|------------------|----------|
| **0.5.x** | 契约冻结 + 切片打磨 + 发布路径 | 2–4 周 | host 默认可拉起；CI 契约绿；0.5 已知问题清零 |
| **0.6** | **P0：最小可用 AIOS Runtime** | +6–8 周 | Intent 闭环 + 真调度 + 默认隔离 + 多维资源硬拒 **验收全勾** |
| **0.7** | 长程可靠 + 效率底座 | +4–6 周 | marathon 恢复率 / cache 仪表 / 三维成本面板 |
| **0.8** | 多 Agent 与进化 **产品化** | +6–8 周 | IPC 协作可演示日用；技能门默认；Eval 出趋势 |
| **0.9** | 人机协作 + Coding Profile | +6–8 周 | 可打断/可解释；编程场景接近主力可用 |
| **1.0** | Personal Agent Runtime GA | 持续 | 稳定 ABI、日用文档、WASM 技能可选常态 |

---

## 4. 阶段 H · 0.5.x — 基于最新版继续打磨（当前主线）

**目标**：不扩功能面；把 0.5 已有零件变成 **默认可依赖、可观测、可发布**。

### 4.1 Hardening 工程清单

| ID | 工作项 | 产出 | 优先级 | 状态 |
|----|--------|------|--------|------|
| **H-01** | Rust host **发布路径打穿** | `start.py` / client 统一 target 优先；缺 binary 明确告警降级 | H0 | ✅ |
| **H-02** | ABI 契约测试常绿 | `kernel-ci.yml`：`cargo test` + host build + Python ABI | H0 | ✅ |
| **H-03** | tool_gate **绕过清单归零** | 伪造 `_tool_gate_passed` 丢弃；create_process 失败 fail-closed | H0 | ✅ |
| **H-04** | Court fail-closed 可配且默认安全 | host 在线无裁决 → deny；文档表 + 单测 | H0 | ✅ |
| **H-05** | 资源 charge **接入 loop/tool 真实路径** | concurrency/child_proc/io 硬拒（不再吞 charge 异常） | H1 | ✅ |
| **H-06** | Intent → cap → **工具 schema 裁剪** 强制 | 显式 caps 下 filter 失败 → 空 tools（fail-closed） | H1 | ✅ |
| **H-07** | Scheduler **驱动** session/workforce 队列 | run_gate + 优先级；session 锁仅同会话互斥（见下） | H1 | ✅ 切片 |
| **H-08** | 沙箱默认 profile | isolation role ↔ computer profile + `degraded_local_flag` | H1 | ✅ |
| **H-09** | Isolation supervisor 雏形 | Rust isolation + end_process 释放；非 OS PID 收割（0.6 加深） | H2 | ✅ 切片 |
| **H-10** | 审计回放：一次 run 可还原 | `GET /api/kernel/runs/{id}/replay` 时间线 | H2 | ✅ |
| **H-11** | Provider cache hit **进周报/Kernel 指标** | cache_metrics + 周报磁盘快照回落 | H2 | ✅ |
| **H-12** | 全量 `backend/tests` + 关键 e2e 进 CI | backend-ci 全量 pytest + Phase H；kernel-ci cargo | H0 | ✅ |
| **H-13** | 包市场生产密钥路径 | [PACKAGE_TRUST.md](./PACKAGE_TRUST.md) | H1 | ✅ |
| **H-14** | host 卡死恢复稳态 | `restart_kernel_host` + 单测 | H1 | ✅ |

> **H-07 / H-09「切片」说明**：调度权威与 isolation 逻辑已在默认路径；彻底去掉 session 锁 / OS 级 wait-reap 属 **0.6 P0 加深**，不阻塞 0.5.x 出口。

### 4.2 产品打磨（少而硬）

| ID | 工作项 | 状态 |
|----|--------|------|
| **HP-01** | Kernel 页：资源账户 + 调度队列 + 链健康 | ✅ API：`/cost` `/cache/metrics` `/runs/{id}/replay` |
| **HP-02** | 高危操作：确认 → 执行 → 可回滚 | 🚧 控制台/checkpoint 已有；undo UX 0.6 继续 |
| **HP-03** | **不做**新大型前台功能 | ✅ 本阶段遵守 |

### 4.3 0.5.x 出口标准

- [x] 默认 `TAKTON_KERNEL_BACKEND=rust` 可 auto-start 或明确降级提示（`start.py` / client）  
- [x] tool_gate / process_access / package trust 相关测 + Phase H 单测  
- [x] 全量 backend CI + **kernel-ci**（cargo ABI）  
- [x] 包信任 / Court fail-closed 文档（PACKAGE_TRUST · KERNEL_RUST）

---

## 5. 阶段 P0 · 0.6 — 最小可用 AIOS Runtime

**目标**：从「能 mediate 的 Agent 应用」变成「能调度、能配额、默认可隔离的 Runtime」。

> H 系列大量工作与 P0 重叠；**0.6 以验收清单打勾为准**，不以功能名称为准。

### 5.1 工程清单（延续并收口）

| ID | 工作项 | 验收要点 | 状态 |
|----|--------|----------|------|
| K-01 | ABI v1 冻结 | 文档 + golden；kernel-ci | ✅ |
| K-02 | host 默认随产品 | auto-start + 构建脚本；vendor 二进制由发布流水线拷贝 | ✅ 路径 / 🧩 二进制 |
| K-03 | Intent 强制闭环 | require_intent + `cap_tools`；pack 扩容后重裁剪 | ✅ |
| K-04 | Scheduler 驱动执行 | run_gate + 优先级；前台先于后台出队（单测） | ✅ |
| K-05 | Resource Manager 接线 | charge 硬拒 + `resource_denied` 审计；memory 超限拦工具 | ✅ |
| K-06 | 沙箱默认化 | workforce sandbox_required fail-closed | ✅ |
| K-07 | Isolation Supervisor | spawn/complete/end 释放；OS 级 wait/reap 继续加深 | ✅ 切片 |
| K-08 | Court 策略下沉 Rust | host 在线 fail-closed；Python 完整层 fallback | ✅ 切片 |
| K-09 | 审计轨迹可回放 | `/api/kernel/runs/{id}/replay` + resource_denied | ✅ |

### 5.2 0.6 验收标准（必须全勾）

- [x] 任意工具路径必经 ABI mediate；HTTP 调试 execute 无 process → 403；入口静态清单  
- [x] 显式能力进程：未授权工具对模型 **不可见**（`cap_tools` + pack 后重滤）**且不可调**（mediate）  
- [x] 双 Agent 抢资源：高优先级可先于低优先级获得执行槽（run_gate / scheduler 单测）  
- [x] 超并发 / 超 child_proc / 超逻辑内存：**拒绝并审计**（`resource_denied` + charge 硬拒）  
- [x] workforce 任务默认沙箱（平台能力允许时；无能力 fail-closed 文案）  
- [x] `cargo test -p takton-kernel` + Python 契约/P0 单测（host 缺省时相关用例 skip）  

**用户感知出口**  
> Agent 永远带着「够用权限」干活；超限可解释；后台不饿死前台；危险动作默认在沙箱；Kernel 页能看懂资源与决策。

---

## 6. 阶段 0.7 — 长程可靠与效率

| ID | 工作项 | 说明 |
|----|--------|------|
| R-01 | Checkpoint + 工具结果落盘统一 | 大结果外置，上下文只留句柄 |
| R-02 | Doom loop / 预算耗尽优雅退出与恢复 UX | 已有雏形，打通前后端文案 |
| R-03 | 上下文泄漏控制 | 子进程结束回收 cap 与资源 |
| R-04 | Provider 缓存命中率仪表 | family 级；进周报 |
| R-05 | Token / billable / 资源三维成本面板 | 编制 + Kernel 统一口径 |

**验收**：固定 marathon（≥2h 模拟）恢复成功率、中断可解释率写入 Eval 阈值。

---

## 7. 阶段 0.8 — 多 Agent 与进化（产品化）

0.5 已交付 **切片**（IPC、系统服务、context_vm、skill_gate、Eval、SDK 等）。本阶段目标是 **默认可用、可演示日用**，不是从零重写。

| ID | 工作项 | 0.5 切片 | 0.8 目标 |
|----|--------|----------|----------|
| M-01 | 内核 IPC | 🧩 | 两独立 Agent 经 IPC 协作成为演示默认路径 |
| M-02 | 系统服务 Memory / Notify | 🧩 | 用户 Agent 只经 syscall，文档+示例齐 |
| M-03 | 记忆分层 + 主动整理 | 🧩 | working/episodic/semantic/skill 可观测 |
| M-04 | 上下文内核配额换入换出 | 🧩 | 长上下文不拖垮宿主 |
| M-05 | 技能验证门 | 🧩 | 未过门不可加载为默认策略 |
| M-06 | Evolution 永不自动改 live | ✅ | 保持硬约束 + 回放门加硬 |
| M-07 | Eval Harness | 🧩 | 固定集出分趋势进 CI/周报 |
| M-08 | Agent SDK + 打包 | 🧩 | 外部最小可复现示例 |

---

## 8. 阶段 0.9 → 1.0 — 平台化与日用

| ID | 工作项 | 0.5 切片 | 后续目标 |
|----|--------|----------|----------|
| E-01 | Coding Profile | 🧩 | engineering / review / pair 打透体验 |
| E-02 | 人机协作打断 / 改 plan / 批准 | 🧩 | collab 一等公民 UX |
| E-03 | ABI 版本策略 | 🧩 | 兼容窗口与破坏次数目标 0 |
| E-04 | WASM Skill Runtime | 🧩 | 技能交付常态 + 限额可解释 |
| E-05 | HAL 路径/命令/浏览器 | 🧩 | 统一经能力与策略 |
| E-06 | 包管理 / 签名扫描 | 🧩 | 生产密钥默认强制 |
| E-07 | 多设备 Instance 迁移 | 🧩 | export/import 可用 |

**1.0 GA 标志**：稳定 ABI、日用文档、可选 WASM 技能、单机日用「敢默认开」。

---

## 9. 明确不做（N）与缓做

| 项 | 原因 |
|----|------|
| ❌ 全仓 Python → Rust | 一年无产品 |
| ❌ 改 Linux/Windows 内核 | 做的是 Agent 层 OS |
| ❌ 对标 OpenClaw 堆多通道 | 稀释定位 |
| ❌ 正面硬刚 Claude Code/Codex 全场景 | 不同赛道；只打「治理下的编程能力」 |
| ❌ 无 Eval 的「智能自动改策略」 | 不可控 |
| ❌ 公有多租户 / SaaS 优先 | 本地单用户优先 |
| ⏸ 重型前端新页面 | 0.5–0.6 冻结功能扩张 |
| ⏸ 新 Provider 堆砌 | 0.4.11 矩阵已覆盖；优先打磨计量与缓存仪表 |

---

## 10. 对标与叙事

| 对象 | 关系 | 我们怎么打 |
|------|------|------------|
| Claude Code / Codex | 不同赛道 | 不拼纯编程极致；拼 **编程只是能力之一 + 可治理本地 Runtime** |
| Hermes 类 | 近邻 | 更强治理/安全控制平面 + 桌面工作站 |
| 聊天套壳 | 已甩开 | 继续拒绝堆通道、堆前端 |

---

## 11. 度量（用数据迭代）

| 指标 | 说明 | 阶段 |
|------|------|------|
| `mediate_deny_rate` / 可解释率 | 拒绝是否带 layer+rule | 0.5.x–0.6 |
| `budget_exhaust_graceful` | 耗尽是否优雅停 | 0.6 |
| `scheduler_wait_p95` | 排队公平性 | 0.6 |
| `sandbox_default_coverage` | 工具执行默认隔离比例 | 0.6 |
| `cache_hit_rate`（按 family） | 成本 | 0.7 |
| `marathon_resume_success` | 长程恢复 | 0.7 |
| `skill_ship_with_gate_rate` | 技能过门再上线 | 0.8 |
| `eval_suite_score` | 固定集总分趋势 | 0.8 |
| `abi_break_count` | 破坏兼容次数（目标 0） | 全程 |
| `tool_gate_bypass_count` | 代码审计绕过数（目标 0） | 0.5.x |

---

## 12. 执行节奏（务实）

### 每两周

1. **1 个内核硬项**（H-xx / K-xx）  
2. **1 个可演示验收**（控制台可见 / smoke）  
3. **0 个新大型前台功能**（0.6 前）  

### 精力分配（0.5.x → 0.6）

| 比例 | 投入 |
|------|------|
| ~70% | Rust Kernel / ABI / 调度 / 资源 / 隔离 / host 发布 |
| ~15% | Python 脑侧接线（Intent、loop、tool schema） |
| ~10% | 观测 / 文档 / CI / Eval |
| ~5% | 关键回归 bugfix |

---

## 13. 近期 90 天作战图（从 0.5.0-alpha 起算）

```text
Week 1–2   H-01/H-02/H-12  host 发布 + 契约 CI + 全量测
Week 3–4   H-03/H-06       tool_gate 归零 + Intent→工具裁剪强制
Week 5–6   H-05/H-07       资源硬拒接线 + Scheduler 驱动队列
Week 7–8   H-08/H-09       沙箱默认 profile + Isolation supervisor
Week 9–10  H-10/HP-01      审计回放 + Kernel 观测页
Week 11–12 R-01/H-11       结果落盘 + cache 仪表；0.6 验收清单打勾
```

**90 天出口 = 0.6**  
工程：Python 脑可热更；Rust 身崩溃不拖垮 UI；契约测试锁死语义。  
产品：够用权限、超限可解释、默认沙箱、Kernel 页可读。

---

## 14. 代码树映射

| 路线图模块 | 主要路径 |
|------------|----------|
| Rust Kernel | `crates/takton-kernel` |
| Runtime / Host | `crates/takton-runtime`, `crates/takton-kernel-host` |
| Python 适配 | `backend/kernel_rust/`, `backend/kernel/kernel.py` |
| tool_gate / court | `backend/kernel/tool_gate.py`, `permission_court.py` |
| Intent / 能力 | `backend/kernel/intent.py`, `capability.py` |
| 资源 / 调度 | `backend/kernel/resource_os.py`, `scheduler.py`, `llm_admission.py` |
| Loop / 工具 | `backend/agent/loop*.py`, `tool_hooks.py`, `phases/*` |
| Provider / 成本 | `backend/services/llm/provider_profiles.py`, `usage_normalize.py` |
| 沙箱 | `backend/computer/*` |
| 编制 | `backend/kernel/dispatcher.py`, `inbox.py`, `identity.py` |
| 进化 | `backend/evolution/*` |
| 观测 API | `backend/api/routes/kernel.py` |
| 桌面 | `frontend/`, `electron/` |
| Eval / smoke | `scripts/takton_eval.py`, `scripts/smoke_*.py` |

---

## 15. 一句话路线图

> **收窄场景、做硬内核、脑身分离、契约驱动、Eval 度量。**  
> 从 0.5 的「控制平面零件齐全」走到 0.6 的「默认可依赖的最小 AIOS Runtime」，再走到 1.0 的日用 Personal Agent Runtime——**靠打磨默认路径，不靠堆功能。**

---

## 16. 修订记录

| 日期 | 变更 |
|------|------|
| 2026-07-31 | 初版：0.4.11 + Rust Kernel 0.1 综合意见 |
| 2026-07-31 | **重写**：以 0.5.0-alpha 为基准；区分切片/默认可依赖；主线改为 H 打磨 → 0.6 验收；P1/P2 改为产品化阶段 |
| 2026-07-31 | **阶段 H 收口**：H-01…H-14 实现/单测/CI/文档勾选；H-07/H-09 标切片、0.6 加深 |
| 2026-07-31 | **P0 里程碑收口**（产品版本维持 **0.5.0-alpha**）：cap_tools / resource_denied / tools API 门控 / 验收单测 |
| 2026-07-31 | 明确：路线图阶段名与产品号解耦，不因 P0 升号 |
