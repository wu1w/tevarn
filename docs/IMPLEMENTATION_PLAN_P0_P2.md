# Takton P0–P2 分步开发计划

**配套文档**：`docs/ROADMAP.md`（战略）· `docs/KERNEL_RUST.md`（Rust 基线）  
**语言策略（强制）**：见 §0  
**基准**：0.4.11-alpha + `crates/takton-kernel` 0.1.0  
**更新**：2026-07-31

---

## 0. 语言与迁移铁律（全阶段适用）

### 0.1 默认规则

| 场景 | 语言 |
|------|------|
| **新建** Kernel / Runtime / 调度 / 权限 / 资源 / 审计 / IPC / 隔离 / 系统服务 | **Rust** |
| **改动** 已有 `backend/kernel/*`、`backend/runtime/*`、与控制平面相关的逻辑 | **优先 Rust 重写并接线**，Python 只留薄适配层 |
| **保留 Python** | Agent 脑：Planner、LLM 调用、工具业务实现、RAG 向量检索、SQLAlchemy 业务表、FastAPI 路由壳、Evolution 蒸馏启发式（可逐步迁） |
| **禁止** | 在 Python 里继续堆「第二个内核」；新功能先写 Python 内核再「以后迁」 |

### 0.2 迁移切片标准流程（每个模块都走）

```text
1. 冻结行为：黄金测试 / 契约 JSON（Python 现状或已有 Rust）
2. Rust 实现（crate 内纯逻辑 + host RPC 方法）
3. Python 适配：backend/kernel_rust 或 薄 shim 转发
4. feature flag / 默认切 Rust
5. 对照测试绿 → 删除或掏空 Python 实现（只留 re-export 警告）
```

### 0.3 目标 crate 演进

```text
crates/
  takton-kernel/          # 控制平面核心（已有，持续扩）
  takton-runtime/         # Runtime 门面（已有）
  takton-kernel-host/     # JSON-RPC / 后续 gRPC host（已有）
  takton-court/           # P0：完整权限法院（可先模块后拆 crate）
  takton-isolation/       # P0：沙箱监督
  takton-ipc/             # P1：Agent 间消息
  takton-services/        # P1：系统服务框架
  takton-skill-gate/      # P1：技能验证门
  takton-eval/            # P1：评测 runner（可 CLI）
  takton-sdk/             # P2：Agent 打包/ABI 工具
```

### 0.4 Python 最终形态

```text
backend/kernel/          → 几乎全是 re-export / 业务适配（identity DB、dispatcher 调 loop）
backend/kernel_rust/     → 唯一控制平面客户端
backend/agent/           → 脑：loop、context、tools 业务
backend/api/routes/      → HTTP 薄壳，调 Rust host 或 Python 脑
```

---

## 1. 总览：阶段 · 版本 · 工期

| 阶段 | 版本目标 | 工期（单人全职粗估） | 一句话出口 |
|------|----------|----------------------|------------|
| **P0-A** 契约与默认 Rust | 0.5.0 | 2 周 | ABI 锁死；host 必达；Python 内核只作 fallback |
| **P0-B** 强制闭环 | 0.5.x | 2 周 | Intent→能力→工具 schema 强制 |
| **P0-C** 调度与资源 | 0.6.0-alpha | 3 周 | 真调度 + 多维资源接线 |
| **P0-D** 隔离与法院 | 0.6.0 | 3 周 | 默认沙箱 + Court 进 Rust |
| **P0 缓冲** | 0.6.x | 1 周 | 验收清单 + 删 Python 死代码 |
| **P0.5** 长程与效率 | 0.7.0 | 4–6 周 | 小时级可靠 + 缓存指标 |
| **P1-A** IPC 与服务 | 0.8.0-alpha | 4 周 | Agent 可通信 + 系统服务 |
| **P1-B** 进化门与 Eval | 0.8.0 | 4–6 周 | 技能可验证 + 固定评测 |
| **P2-A** Coding 打透 | 0.9.0 | 6–8 周 | 编程场景日用级 |
| **P2-B** 平台化 | 1.0.0 | 持续 | SDK / WASM / HAL / 包管理 |

**P0 合计约 11 周** · **P1 约 8–10 周** · **P2 约 3 个月+**

依赖关系：

```text
P0-A ──► P0-B ──► P0-C ──► P0-D ──► P0.5 ──► P1-A ──► P1-B ──► P2
              │         │
              └─────────┴── 可部分并行：观测 UI / 文档
```

---

# P0 — 最小可用 AIOS Runtime

## P0-A · 契约冻结与 Rust 默认生产（Week 1–2）→ `0.5.0`

**状态：✅ 已落地（2026-07-31）** — 见 `docs/kernel-abi-v1.md`、`test_abi_rust.py`、`crates/takton-kernel/tests/abi_v1.rs`

### 目标

控制平面**权威在 Rust**；Python `AgentKernel` 仅测试/fallback。

### 步骤

| 步 | 任务 | 语言 | 落点 | 验收 |
|----|------|------|------|------|
| A1 | 写 **ABI v1**（方法、错误码、进程/事件 JSON schema） | 文档 | `docs/kernel-abi-v1.md` | ✅ |
| A2 | 契约测试：Rust `#[test]` + Python 调 host 的黄金用例 | Rust+Py | `tests/abi_v1.rs` · `test_abi_rust.py` | ✅ |
| A3 | Host 补齐缺失 RPC（`abi_version`/`list_methods`/`get_escalation`/…） | Rust | `takton-kernel-host` | ✅ |
| A4 | **Release 构建 + vendor 安装路径** | 脚本 | `build-kernel-host.ps1` · `vendor/` | ✅ |
| A5 | `agent_kernel_backend` **默认 rust**；无 host 时显式 error 告警 | Py 薄 | `get_kernel()` | ✅ |
| A6 | Electron/start 生命周期：先起 host 再起 backend | 脚本/TS | `start.py` / `electron/main.ts` | ✅ |
| A7 | 标记 Python 内核模块 **DEPRECATED** | Py | kernel/process/capability/… | ✅ |

### 本步替换/冻结的 Python

| Python 模块 | 动作 |
|-------------|------|
| `kernel/kernel.py` AgentKernel 主体 | **停止新功能**；仅 fallback |
| `kernel/process.py` / `capability.py` / `scheduler.py` / `audit_store.py` | 新逻辑只进 Rust；Py 可改为 thin wrap |
| `runtime/*` | 门面继续调 `get_kernel()`；host 启动逻辑可逐步迁 `takton-runtime` |

### 交付物

- [ ] `docs/kernel-abi-v1.md`
- [ ] ABI 测试绿
- [ ] `0.5.0` tag：release host + 默认 rust 后端

---

## P0-B · Intent → 能力 → 工具强制闭环（Week 3–4）→ `0.5.x`

**状态：已落地（2026-07-31）**

### 目标

Agent **永远只带着够用能力**出发；工具 schema 与能力集一致。

### 步骤

| 步 | 任务 | 语言 | 落点 | 验收 |
|----|------|------|------|------|
| B1 | 完善 Rust `intent`：grantable/risky、父 token narrow、TTL | Rust | `takton-kernel/src/intent.rs` | 单测覆盖 risky 剔除 |
| B2 | Host：`apply_intent` / `synthesize_and_issue` RPC | Rust | host | 一次调用完成 cap+token |
| B3 | `create_process` 路径：有 intent 则强制合成能力；禁止「静默全开」主路径 | Rust | `kernel.rs` | 配置 `require_intent` |
| B4 | **工具 schema 裁剪服务**（cap → tool names） | Rust | 新模块 `tool_catalog.rs` 或 court | mediate 前模型看不到无权限工具 |
| B5 | Python loop：创建/create 后只注册裁剪后 tools；删本地「全量注册再拦」 | Py 薄接线 | `loop.py` / `loop_tools.py` | 模型侧 tools 列表 ⊆ caps |
| B6 | grant_store / TOOL_TO_CREW_CAP **迁入 Rust** 词表 | Rust | `capability` 或 `catalog` | Python 只读生成物或 RPC |
| B7 | 提权：Rust request → 事件；Python 只做通知/DB 档案写入 | Rust 主 | 已有 escalation | 扩权唯一通道仍在内核 |

### 替换的 Python

| 模块 | 动作 |
|------|------|
| `kernel/intent.py` | 逻辑迁 Rust；Py re-export 调 RPC 或删 |
| `agent/grant_store.py` 映射表 | 权威改 Rust；Py 缓存副本可接受短期 |
| loop 内能力拼装 | 改为调用 host |

### 验收场景

1. 只读 intent → `file_write` 不可见且 mediate deny  
2. 无 `allow_risky` → terminal 在 dropped  
3. 子进程 caps ⊆ 父进程  

---

## P0-C · 真调度 + 资源接线（Week 5–7）→ `0.6.0-alpha`

### 目标

进程从「台账」变成「可调度实体」；资源账户影响执行。

### 步骤

| 步 | 任务 | 语言 | 落点 | 验收 |
|----|------|------|------|------|
| C1 | Scheduler：优先级档（foreground/background/system）、aging、cancel | Rust | `scheduler.rs` 增强 | 单测饥饿/提档 |
| C2 | **RunQueue**：session/workforce 提交任务到 Rust 队列 | Rust | `runtime` + host RPC `schedule_run` | 队列 stats API |
| C3 | Python loop：**取号执行**——拿到 lease 才跑 LLM/tool | Py 接线 | `loop.py` · dispatcher | 无 lease 不调用 LLM |
| C4 | LLM admission **迁 Rust**（槽位、owner 预留、日配额） | Rust | 新 `llm_admission.rs` | 替换 `llm_admission.py` 权威 |
| C5 | Resource：tool 调用 `resource_charge(tool_calls)`；大结果 `memory_bytes` | Rust+接线 | kernel + loop_tools | 超限 deny + 审计 |
| C6 | child_proc：与 computer spawn 挂钩（先计数，后硬隔离） | Rust | resource + 后续 isolation | 超限拒绝 spawn |
| C7 | soft_renew / top_up / auto_tighten 仅保留 Rust | Rust | 已有 | 删 Python 重复逻辑 |
| C8 | 观测：`/api/kernel/scheduler` · `/api/kernel/resources` | Py 薄 API | `routes/kernel.py` | 控制台可展示 |

### 替换的 Python

| 模块 | 动作 |
|------|------|
| `kernel/scheduler.py` | 删除实现，RPC 代理 |
| `kernel/llm_admission.py` · `llm_priority.py` · `llm_quota.py` · `llm_scheduler.py` | 迁 Rust 后删权威 |
| dispatcher busy 集合 | 逐步改为 kernel concurrency 资源 |

### 验收场景

1. 高优先级 chat 抢在低优先级 background 前获得 LLM 槽  
2. `tool_calls` 配额耗尽 → mediate/执行拒绝  
3. 同 identity 并发槽=1 时第二单排队不双跑  

---

## P0-D · 默认隔离 + 权限法院 Rust 化（Week 8–10）→ `0.6.0`

### 目标

硬隔离兜底；完整 court 在 Rust 热路径。

### 步骤

| 步 | 任务 | 语言 | 落点 | 验收 |
|----|------|------|------|------|
| D1 | **Isolation Supervisor** crate/模块：spawn/wait/kill | Rust | `takton-isolation` 或 `kernel/isolation.rs` | 单元+集成 |
| D2 | 后端适配：bwrap / seatbelt / Job / WSL 由 Rust 调或监督 Python helper | Rust 主 | isolation | 至少 1 平台 E2E |
| D3 | Profile：`interactive` / `workforce` / `untrusted` 默认沙箱策略 | Rust | profiles 配置 | workforce 默认非 local |
| D4 | 将 `permission_court.decide_tool` 层迁 Rust：secret_floor、user deny、path | Rust | `court.rs` 扩展 | 与 Py 黄金用例一致 |
| D5 | path 白名单 / dangerous_paths 规则表进 Rust | Rust | court 配置加载 | 拒绝写系统目录 |
| D6 | steward/workforce 规则：身份 caps 由内核持有快照 | Rust | kernel + identity 同步 | 无 DB 热路径 |
| D7 | Python `tool_hooks` 改为 **只调 Rust decide_tool** | Py 薄 | `tool_hooks.py` | 本地无重复裁决 |
| D8 | 高危 **确认-回滚**：写文件前 Rust 记快照句柄（或调现有 file_checkpoint） | Rust+接线 | 新 RPC + agent | 控制台可 undo 入口 |
| D9 | 决策轨迹打包：run 结束导出 events 子集 | Rust | audit | API 可下载 |

### 替换的 Python

| 模块 | 动作 |
|------|------|
| `kernel/permission_court.py` | 权威迁 Rust；Py 删逻辑 |
| `computer/*` 管理策略 | 监督权归 isolation；具体 backend 可暂留 Py 被 Rust 调用 |
| `agent/tool_hooks.py` 裁决 | 仅 RPC |

### 验收场景

1. workforce 任务默认沙箱（平台支持时）  
2. path deny 不依赖 Python court  
3. 沙箱子进程崩溃 → 资源账户释放  

### P0 总验收清单（0.6.0）

- [ ] 默认 backend=rust，无 Python 内核也能完成 chat 工具路径  
- [ ] Intent 闭环 + schema 裁剪  
- [ ] 调度+LLM 槽在 Rust  
- [ ] 资源超限可解释拒绝  
- [ ] Court 主路径在 Rust  
- [ ] 沙箱默认策略生效  
- [ ] `backend/kernel/{process,capability,scheduler,permission_court,llm_*}.py` 无业务逻辑或仅 shim  

---

# P0.5 / 0.7 — 长程可靠与效率（Week 11–16）

| 步 | 任务 | 语言 | 落点 | 验收 | 状态 |
|----|------|------|------|------|------|
| E1 | Checkpoint 引擎进 Rust（快照 + tail_hash 增量） | Rust | `process_snapshot.rs` + RPC | 恢复不用全量 replay | ✅ |
| E2 | 工具大结果 **外置存储 + 句柄进上下文** | Rust | `result_store.rs` + `normalize_tool_result` | 上下文不炸 | ✅ |
| E3 | Doom loop / iteration 预算进内核策略 | Rust | `policy.rs` + loop/tool_round | 触发即 suspend/end | ✅ |
| E4 | 进程树回收：子终态 cap/资源强制释放 | Rust | `end_process` + `reclaim_process_tree` | 无泄漏 | ✅ |
| E5 | Provider 缓存指标 + 上报 | Rust+Py | `cache_metrics` + `log_cache_usage` | cache_hit_rate 可查 | ✅ |
| E6 | 马拉松 Eval / soak | Py | `smoke_p05_marathon` + `marathon_soak` | resume 成功率阈值 | ✅ |
| R3 | persistence 投影 Rust snapshot | Py | `project_rust_snapshots` | full_replay=false | ✅ |
| R4 | 退出文案 / 恢复入口 | Py | `exit_reasons` + `/kernel/policy` | 可解释 | ✅ |
| R5 | 三维成本面板 | Rust+API | `cost.rs` + `/kernel/cost` | token/billable/resource | ✅ |

**状态**：**P0.5 完成**（`docs/P05_COMPLETION_REPORT.md`）。  
**替换**：进程快照权威在 Rust；`persistence.py` 启动时投影到 DB；事件 JSONL 仍为审计源。

---

# P1 — 多 Agent 承载力与进化（0.8）

**状态：✅ 已完成（2026-07-31）** — 见 `docs/P1_COMPLETION_REPORT.md`

## P1-A · IPC + 系统服务

| 步 | 任务 | 语言 | 落点 | 验收 | 状态 |
|----|------|------|------|------|------|
| F1 | IPC 总线：点对点消息、内核鉴权 | Rust | `ipc.rs` | 未授权 send deny | ✅ |
| F2 | `ipc_send/recv` RPC + 背压 | Rust | host | 单测 + smoke | ✅ |
| F3 | 系统服务框架：注册 / 健康 / 特权级 | Rust | `services.rs` | 服务表可列 | ✅ |
| F4 | **Memory 系统服务** | Rust | `sys_memory_*` | Agent 不直连表 | ✅ |
| F5 | **Notify 系统服务** | Rust | `sys_notify_*` | 可推送/ack | ✅ |
| F6 | Identity 热数据缓存 | Rust | `identity_cache.rs` | 不 await ORM | ✅ |
| F7 | Inbox claim 内核化 | Rust | `inbox.rs` | 双 worker 不双派 | ✅ |

## P1-B · 技能验证门 + Eval + SDK 雏形

| 步 | 任务 | 语言 | 落点 | 验收 | 状态 |
|----|------|------|------|------|------|
| G1 | Skill package 格式（manifest + 哈希） | Rust | `skill_gate.rs` | | ✅ |
| G2 | 验证流水线 | Rust | verify 门 | 失败不可 activate | ✅ |
| G3 | 版本 + 回滚 RPC | Rust | `skill_rollback` | 一键回滚 | ✅ |
| G4 | Evolution auto_apply=false 硬编码 | Rust+Py | evolution config/manager | 永不 auto live | ✅ |
| G5 | Eval Harness 4 套 | Py | `scripts/takton_eval.py` | overall≥0.75 | ✅ |
| G6 | 最小 Agent SDK | 文档+脚本 | `docs/agent-sdk.md` · `takton_sdk_pack.py` | 清单可校验 | ✅ |
| G7 | 记忆分层整理 | Rust | `memory_layers.rs` | consolidate 可观测 | ✅ |

另：**M-04** 上下文配额换入换出 → `context_vm.rs` ✅

---

# P2 — 日用与平台化（0.9 → 1.0）

## P2-A · Coding Profile 打透 + 人机协作

**状态：✅ 完成** — `docs/P2_COMPLETION_REPORT.md`

| 步 | 任务 | 语言 | 落点 | 状态 |
|----|------|------|------|------|
| H1 | Coding profile：工具集、沙箱、预算模板 | Rust | `coding_profile.rs` | ✅ |
| H2 | 可打断：suspend；改 plan 后 resume | Rust | `collab.rs` | ✅ |
| H3 | 文件编辑确认/diff/回滚 | Rust | `edit_session.rs` | ✅ |
| H4 | 上下文与 repo 索引配额 | Rust | `repo_index.rs` | ✅ |
| H5 | 编程 Eval 周更 | Py | `takton_eval.py` | ✅ |

## P2-B · WASM / HAL / 包管理 / 多设备

| 步 | 任务 | 语言 | 落点 | 状态 |
|----|------|------|------|------|
| I1 | WASM skill runtime + 资源限额 | Rust | `wasm_runtime.rs` | ✅ |
| I2 | HAL：路径/命令/浏览器统一接口 | Rust | `hal.rs` | ✅ |
| I3 | 包管理：安装/签名/依赖 | Rust | `package_mgr.rs` | ✅ |
| I4 | 多设备 Instance 迁移 | Rust | `instance.rs` | ✅ |

---

## 2. 每步的「Definition of Done」（通用）

每个编号步骤合并前必须：

1. **Rust 单测**覆盖核心分支  
2. **ABI/集成测试**至少 1 条 Python↔host  
3. **无新增** Python 内核权威逻辑  
4. 若替换旧模块：旧文件变为 shim 或删除，并更新 import  
5. 更新 `docs/KERNEL_RUST.md` 或 ABI 文档  
6. Security/观测路径不回退（拒绝必有 reason/layer）

---

## 3. 模块迁移对照表（Python → Rust）

| Python 现状 | 目标 Rust | 阶段 |
|-------------|-----------|------|
| `kernel/process.py` | `process.rs` | ✅ 已有 → A 删权威 |
| `kernel/capability.py` · `signing.py` | `capability.rs` | ✅ → A |
| `kernel/kernel.py` 主体 | `kernel.rs` + host | ✅ → A 默认 |
| `kernel/audit_store.py` | `audit.rs` | ✅ → A |
| `kernel/scheduler.py` | `scheduler.rs` | C |
| `kernel/intent.py` | `intent.rs` | B |
| `kernel/permission_court.py` | `court.rs` 扩展 | D |
| `kernel/llm_*` | `llm_admission.rs` | C |
| `kernel/persistence.py` | checkpoint in kernel | 0.7 |
| `kernel/shared_store.py` | 可选；单机不需要 Redis | 弱化 |
| `computer/manager` 策略 | isolation supervisor | D |
| `kernel/inbox.py` 队列 | ipc + queue | P1-A |
| `kernel/dispatcher.py` 编排核心 | runtime 调度 + 薄 Py 脑唤醒 | P1-A |
| `kernel/identity.py` 运行时 | services + cache | P1-A |
| `evolution` 门禁 | skill-gate | P1-B |
| `agent/loop.py` | **保留 Python 脑** | 全程 |
| `services/llm/*` | **保留 Python**（可后迁 adapter） | 全程 |
| `tools/*` 业务 | **保留 Python**；执行入口经隔离 | 全程 |

---

## 4. 双周冲刺排期示例（前 12 周 = P0）

| Sprint | 周 | 聚焦 | 出口 |
|--------|-----|------|------|
| S1 | 1–2 | P0-A ABI + 默认 Rust + 安装 | 0.5.0 |
| S2 | 3–4 | P0-B Intent 闭环 + catalog | 0.5.2 |
| S3 | 5–6 | P0-C 调度 + LLM admission | 调度 API 可用 |
| S4 | 7 | P0-C 资源接线收尾 | 0.6.0-alpha |
| S5 | 8–9 | P0-D Court 迁入 + 沙箱默认 | court 黄金测试 |
| S6 | 10–11 | P0-D Isolation + 验收 + 删 Py 死代码 | **0.6.0** |
| S7 | 12 | 缓冲 / 文档 / 马拉松骨架 | 进入 0.7 |

---

## 5. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 单人带宽 | 严格按步；每步可合并可演示；禁止并行开三个大模块 |
| 行为漂移 | 每模块先黄金测试再删 Py |
| Windows 沙箱弱 | Isolation 先 Job Object + 计数；Linux CI 跑 bwrap |
| loop 与调度耦合难 | 先「lease 门闩」不先重写整个 loop |
| 过早 WASM/SDK | 锁在 P2；P0 不做 |

---

## 6. 立即开工的第一刀（本周）

按本计划，**唯一推荐的启动序列**：

1. **A1** 写 `docs/kernel-abi-v1.md`（从现有 host 方法反推）  
2. **A2** 补 `test_abi_rust.py` + Rust 侧 ABI 测试  
3. **A5–A6** 保证产品路径默认 Rust host  
4. 然后进入 **B1–B5 Intent 闭环**（第一个用户可感知的 OS 能力）

---

## 7. 修订记录

| 日期 | 变更 |
|------|------|
| 2026-07-31 | 初版：P0–P2 分步计划；默认 Rust 替换 Python 控制平面 |
