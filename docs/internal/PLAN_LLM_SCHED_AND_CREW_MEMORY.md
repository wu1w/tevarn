# 完整方案：LLM 资源公平调度 + 编制记忆

> **范围**：Takton Alpha（编制 / AIOS 线）  
> **版本锚点**：1.0.0-alpha 之上增量  
> **原则**：不新开记忆后端；不引入 eBPF/宿主内核；调度先 **可解释规则**，再谈聪明路由  
> **目标**：两项都「完整」= 语义闭环 + API + UI + 测试 + 可演示，而非只写半截配置项

---

## 0. 完成定义（DoD）

### 0.1 LLM 资源公平调度 — 完成

| # | 条件 |
|---|------|
| S1 | 全局存在 **LLM 请求槽位**（in-flight）与 **排队**；主人对话优先于后台工单 |
| S2 | 编制内串行（已有）+ 全局并发 + **加权公平**（防饿死）可配置 |
| S3 | 工单级 / 员工日级 / 全局日级 **token 预算** 可查、可耗尽、耗尽终态正确 |
| S4 | 任务类预算地板与档案默认合并策略 **唯一实现**（已有 `workforce_budget` 收口） |
| S5 | `GET /kernel/scheduler/status` + 内核页「调度」面板：在飞、排队、配额 |
| S6 | 领域事件：`scheduler.queued` / `scheduler.granted` / `scheduler.throttled` / `budget.*` |
| S7 | 单测：公平轮转、主人抢占、预算 fail、并发上限 |

### 0.2 编制记忆 — 完成

| # | 条件 |
|---|------|
| M1 | 读优先级写死并测：Identity Memory ≫ session ≫ entities/wiki/graph |
| M2 | 写路径门禁：distilled 必 `approved_by`；budget/失败默认不写 experience |
| M3 | 注入策略完整：sticky kinds 全量；experience 限条数/限长；超阈检索或截断 |
| M4 | 沉淀策略完整：完工可选摘要、废止 supersede、资料卡 CRUD 语义 |
| M5 | API：`memory` 列表/追加/废止 + 注入预览 `memory/preview` |
| M6 | UI：资料卡记忆 tab 可废止；派活前可预览「将注入哪些记忆」 |
| M7 | 单测：注入顺序、experience 上限、失败不沉淀、审批写入 |

### 0.3 联合 DoD

- 调度决定 **谁何时跑**；记忆决定 **跑的时候带着谁的人设**  
- 演示脚本：`examples/demo_scheduler_memory/` 一键可讲清两者

---

## 1. 总架构

```
                    ┌──────────────────────────────────────┐
  主人 Chat Loop ──►│         LlmAdmissionController        │
  员工 Dispatcher ─►│  (全局单例 · 槽位 · 优先级队列 · 配额)  │
                    └───────────────┬──────────────────────┘
                                    │ acquire(lease)
                                    ▼
                         LLM call / charge_tokens
                                    │
  Dispatcher 唤醒 ──► IdentityMemoryAssembler ──► prompt 前缀
                      (只读编制真源 · 瘦身注入)
                                    │
  完工 / 述职 ─────► IdentityMemoryWriter
                      (门禁 · 审批 · 不污染)
```

| 新模块（建议路径） | 职责 |
|--------------------|------|
| `backend/kernel/llm_scheduler.py` | 准入控制、队列、租约、统计 |
| `backend/kernel/quota.py` | 日配额 / 员工配额记账（SQLite 或进程内+落盘） |
| `backend/kernel/crew_memory.py` | 组装注入块 + 沉淀策略（调用 IdentityRegistry） |
| `backend/api/routes/kernel.py` 扩展 | scheduler/status、memory/preview、沉淀配置 |
| FE 内核页 Tab「调度」 | 可视化 |
| FE 资料卡记忆增强 | 废止 / 预览 |

**不新建记忆表族**：继续 `IdentityMemoryEntry`。

---

## 2. LLM 资源公平调度 — 详细设计

### 2.1 现状（零件）

| 已有 | 缺口 |
|------|------|
| `agent_dispatcher_max_global_concurrent` / `_identity_concurrent` | 管的是 **工单并发**，不是 **LLM HTTP 并发** |
| `charge_tokens` / process `token_budget` | 无 **全局/日配额**；无排队语义 |
| `workforce_budget` 抬预算 | 与调度器未统一入口 |
| 身份 busy 串行 | 无 **跨身份公平**、无 **主人优先** |

### 2.2 核心对象

```python
# llm_scheduler.py（概念）

class Priority(IntEnum):
    OWNER_CHAT = 100      # 主人正在聊
    INTERACTIVE = 80      # 主人触发的同步动作
    WORKFORCE_HIGH = 50   # 高优工单
    WORKFORCE_NORMAL = 30
    WORKFORCE_LOW = 10
    BACKGROUND = 5        # cron / 静默

@dataclass
class LlmLeaseRequest:
    request_id: str
    source: str           # "chat" | "workforce" | "cron" | "subagent"
    identity_id: str | None
    session_id: str | None
    process_id: str | None
    inbox_item_id: str | None
    priority: Priority
    enqueued_at: float
    estimated_tokens: int  # 可选，用于粗限流
    # 公平：虚拟完成时间 / wait boost
    wait_boost: float = 0.0

@dataclass  
class LlmLease:
    request_id: str
    granted_at: float
    # 持有期间 charge 归属 process_id / identity_id
```

### 2.3 准入算法（v1 可解释，完整）

**配置**（`settings` / env）：

| 键 | 默认 | 含义 |
|----|------|------|
| `llm_max_in_flight` | 4 | 全局同时 LLM 请求数 |
| `llm_max_in_flight_per_identity` | 1 | 每员工同时 LLM 请求 |
| `llm_owner_reserve_slots` | 1 | 预留给主人对话的槽（从全局里划） |
| `llm_queue_max` | 64 | 排队上限，超出拒绝并事件 |
| `llm_fairness_wait_weight` | 1.0 | 等待时间加权，防饿死 |
| `llm_daily_token_budget_global` | 0 | 0=不限制；>0 日全局硬顶 |
| `llm_daily_token_budget_per_identity` | 0 | 员工日顶 |

**排序键（越大越先）**：

```
score = priority
      + fairness_wait_weight * min(wait_seconds, 300) / 10
      - (identity 已在飞 ? 1000 : 0)
```

**授予规则**：

1. 若 `in_flight >= llm_max_in_flight` → 排队  
2. 若该 identity 已达 per-identity 上限 → 排队  
3. 若主人请求且 `owner_in_flight < owner_reserve` → **可挤占**：暂不杀后台，但下一空槽只给主人；可选 v1.1：后台 lease 可标记 soft-cancel  
4. 日配额用尽 → **拒绝**（工单 fail terminal / chat 返回人话错误）  
5. 授予后 `lease` 必须在 `finally` **release**（超时 watchdog 回收）

### 2.4 接入点（必须全部挂上才算完整）

| 调用点 | 改造 |
|--------|------|
| `agent/phases/llm_round.py`（或 loop 调 LLM 前） | `async with llm_scheduler.acquire(...)` |
| workforce loop 同路径 | `source=workforce`, priority 由 inbox priority 映射 |
| subagent mini-run | `source=subagent`, priority ≤ parent |
| cron / channel | `source=cron`, BACKGROUND |

**禁止**：任何绕过 acquire 的 `LLMServiceFactory` 直调（grep 门禁测试）。

### 2.5 与 token 预算的关系

```
档案 default_token_budget     → 地板
workforce_budget 任务抬升     → 本工单 process.token_budget
charge_tokens                 → 进程内硬顶（已有）
quota 日配额                  → 跨工单累计（新增）
llm_max_in_flight             → 时间维度公平（新增）
```

Budget 超限路径保持：`is_budget_exceeded_result` → inbox fail terminal → 不写 experience。

### 2.6 API

```
GET  /kernel/scheduler/status
{
  "in_flight": [...],
  "queued": [...],
  "config": {...},
  "quota": {
    "global_used_today": N,
    "global_limit": N|null,
    "by_identity": [{id, name, used, limit}]
  }
}

POST /kernel/scheduler/config   # 可选：热更新并发（settings）
```

### 2.7 UI

- 内核页新增 Tab **「调度」**：在飞列表、排队列表、配额条  
- 驾驶舱晨报可选一行：「X 个工单排队 LLM」  
- 设置页（高级）：`llm_max_in_flight` 滑条  

### 2.8 事件

| kind | 何时 |
|------|------|
| `scheduler.queued` | 入队 |
| `scheduler.granted` | 获槽 |
| `scheduler.released` | 释放 |
| `scheduler.rejected` | 队列满 / 日配额尽 |
| `budget.exceeded` | 已有，保持 |

### 2.9 测试

| 用例 | 断言 |
|------|------|
| 并发上限 | 第 5 个请求 queued（max=4） |
| 主人优先 | owner 插队到 workforce 前 |
| 公平 | 两员工交替 granted，非严格 FIFO 饿死 |
| 日配额 | 超额 rejected + 工单 fail |
| release | 异常路径 in_flight 归零 |
| 无直调 | 静态检查或 mock 计数 |

### 2.10 实施切片（调度）

| 切片 | 内容 | 估时 |
|------|------|------|
| S-A | `LlmAdmissionController` + acquire/release + 配置 | 2–3d |
| S-B | 挂接 llm_round + workforce loop | 1–2d |
| S-C | 日配额 quota 表/KV + charge | 1–2d |
| S-D | API + 内核页 + 事件 | 2d |
| S-E | 测试 + demo 脚本 | 1–2d |

---

## 3. 编制记忆 — 详细设计

### 3.1 现状（零件）

| 已有 | 缺口 |
|------|------|
| `IdentityMemoryEntry` + kinds | 沉淀策略分散 |
| `add_memory` / `supersede` / `current_memory` | 缺统一 Writer/Assembler |
| dispatcher `_build_memory_block` 瘦身 | 与 loop 主对话注入不一致 |
| MEMORY_AUTHORITY.md | 未全部强制测试与 UI |
| 进化 approve 写记忆 | 完工自动沉淀未产品化 |

### 3.2 模块职责

**`CrewMemoryAssembler`（读）**

```python
async def build_inject_block(
    identity_id,
    instruction: str,
    *,
    mode: Literal["workforce", "chat", "preview"],
) -> MemoryInjectResult:
    """
    返回:
      header, body, entries_used[{id,kind,version}], truncated, token_estimate
    """
```

规则（完整）：

| kind | workforce | chat（联系 TA） |
|------|-----------|-----------------|
| persona, duty | **必注入**（全量，截断单条 2k） | 必注入 |
| methodology, preference | 全量直至条数 cap | 全量直至 cap |
| experience | **最多 K 条**（默认 1～2），每条 ≤ L 字，按 updated 倒序 | 可选，默认 0～1 |
| 总 cap | `agent_identity_memory_full_inject_max` + 新 `experience_max` | 可略松 |

超总条数：  
1) sticky 保；2) experience 先砍；3) 再按与 `instruction` 简单关键词/向量 top-k（向量不可用则截断）。

**`CrewMemoryWriter`（写）**

```python
async def maybe_distill_from_job(item, result, *, status: str) -> None:
    # status != done → return
    # budget/timeout/fail → return
    # settings.crew_memory_auto_distill == False → return
    # 生成短 experience（可规则模板，不必 LLM）或 LLM 摘要
    # add_memory(kind=experience, source=system|distilled, approved_by=...)
    # distilled 若要求审批：走 evolution 或 pending 记忆提案表（v1 可用 system+开关）

async def record_manual(...):  # 资料卡 / 工具
async def supersede(...):
async def tombstone / supersede empty:  # 废止
```

**失败不写**（强制）：

- budget exceeded  
- timeout  
- cancelled  
- grounding block（未真正执行）  

### 3.3 配置

| 键 | 默认 | 含义 |
|----|------|------|
| `crew_memory_experience_max_inject` | 2 | 注入 experience 上限 |
| `crew_memory_experience_max_chars` | 800 | 单条 experience 注入长度 |
| `crew_memory_auto_distill` | false | 完工自动沉淀（默认关，可信） |
| `crew_memory_auto_distill_min_chars` | 200 | 结果太短不沉淀 |
| `crew_memory_require_approve_distill` | true | 自动沉淀仅生成「待批记忆」而非直接写 |

**待批记忆（完整闭环推荐）**：

- 复用 evolution proposal kind=`memory_distill` **或**  
- 轻表字段：`pending_memory_proposals`（若不想扩表，v1 仅 `auto_distill=false` + 手动「从本单生成经验」按钮）

### 3.4 API

```
GET  /kernel/identities/{id}/memory?kind=
POST /kernel/identities/{id}/memory          # 已有则对齐
POST /kernel/identities/{id}/memory/{entry_id}/supersede
POST /kernel/identities/{id}/memory/preview  # body: {instruction, mode}
POST /kernel/identities/{id}/memory/distill-from-item  # body: {inbox_item_id}
```

`preview` 返回将注入的文本与 entry id 列表 —— **产品完整度关键**。

### 3.5 UI

| 位置 | 行为 |
|------|------|
| 资料卡 · 记忆 | 列表、kind 筛选、废止、手动追加 |
| 资料卡 · 今日工单 | 「沉淀为经验」按钮（调 distill-from-item） |
| 派活面板 | 可选「预览该员工记忆注入」 |
| 审批 | memory_distill 提案与进化同一中心（若开启待批） |

### 3.6 与调度的耦合

| 点 | 行为 |
|----|------|
| 注入前 | Assembler 已限长 → 降低 LLM 输入 token → 间接利好调度 |
| 配额紧 | 可降级：只注入 persona+duty（`mode=compact`） |
| 调度 rejected | 不写任何记忆 |

### 3.7 测试

| 用例 | 断言 |
|------|------|
| sticky 必现 | persona/duty 在 block 中 |
| experience cap | 库内 10 条只注入 ≤2 |
| fail 不沉淀 | budget fail 后 current experience 不变 |
| preview 稳定 | 同输入同输出 |
| distilled 门禁 | 无 approved_by 拒绝 |
| 读优先级 | mock 仅 graph 有内容时 workforce 仍以 identity 为准 |

### 3.8 实施切片（记忆）

| 切片 | 内容 | 估时 |
|------|------|------|
| M-A | `CrewMemoryAssembler` 收口 dispatcher + 单测 | 1–2d |
| M-B | `CrewMemoryWriter` + 失败不写 + 手动 distill API | 2d |
| M-C | preview API + 资料卡废止/沉淀按钮 | 2d |
| M-D | 可选待批 distill + 审批展示 | 1–2d |
| M-E | chat 联系 TA 注入对齐 + 文档 | 1d |

---

## 4. 联合里程碑（推荐排期）

```
Week 1     S-A + S-B          调度准入挂上主路径
Week 1–2   M-A + M-B          记忆读写收口
Week 2     S-C + S-D          配额 + 面板
Week 2–3   M-C + M-D          预览 + UI
Week 3     S-E + M-E + Demo   测试与 examples
```

**Demo 脚本**（`examples/demo_fair_sched_memory/`）：

1. 起 3 个假员工工单 + 1 个主人对话 → 面板见主人优先、排队  
2. 给员工写入 persona/duty + 多条 experience → preview 只见 2 条 experience  
3. 触发 budget fail → 记忆条数不变  
4. 完工手动沉淀 → 新 experience 出现  

---

## 5. 风险与非目标

| 风险 | 缓解 |
|------|------|
| 调度死锁 | lease 超时强制 release；单测 |
| 主人永远饿死后台 | wait_boost 上限 + 后台最低份额可选 |
| 自动沉淀变垃圾记忆 | 默认关；模板摘要；可废止 |
| 与 loop 双路径注入不一致 | 唯一 Assembler |
| 范围膨胀成 AIOS 论文调度 | **禁止** v1 上强化学习路由 |

**非目标（本方案不做）**：

- eBPF / cgroup  
- 多机全局调度  
- 第五套记忆存储  
- 无审批全自动改 caps/persona  

---

## 6. 验收清单（产品语言）

- [x] 内核页能看见「谁在用模型、谁在排队、配额还剩多少」  
- [x] 主人发消息时，后台工单会让槽（可观测）  
- [x] 员工资料卡能管理记忆，派活能预览将注入内容  
- [x] 失败工单不会把员工「教坏」  
- [ ] 上述 Demo 5 分钟内可讲完（脚本可选补 `examples/`）  

---

## 7. 文档与代码锚点（落地后更新）

| 文档 | 更新 |
|------|------|
| `MEMORY_AUTHORITY.md` | 链到 Assembler/Writer 与配置键 |
| `AIOS_OPERATOR.md` | 调度面板、记忆预览操作 |
| `CHANGELOG` | 专节 |
| 本文件 | 切片完成后勾选 DoD |

**代码锚点（现状）**：

- `backend/kernel/dispatcher.py` — 唤醒、记忆块、预算  
- `backend/agent/workforce_budget.py` — 任务抬预算  
- `backend/kernel/identity.py` — memory CRUD  
- `backend/kernel/kernel.py` — charge_tokens  
- `backend/core/config.py` — concurrent / fallback budget  

---

---

## 8. 现状对照（为何还没「完整」）

| 层 | 已有零件 | 仍是半截的原因 |
|----|----------|----------------|
| 工单并发 | `dispatcher._max_global_concurrent` / `_max_identity_concurrent` | 管的是 **工单/loop 槽**，不是 **LLM HTTP 在飞** |
| 任务调度雏形 | `kernel/scheduler.py` · `AgentScheduler` | 优先级堆 **未挂** loop/LLM；**不要**在此硬塞 LLM 准入 |
| 进程预算 | `charge_tokens` + `workforce_budget` + precheck | 无全局/日配额、无排队、无主人预留槽 |
| 记忆注入 | `dispatcher._build_memory_block` + 单测 | **仅编制路径**；chat「联系 TA」未共用；无 `preview` |
| 经验沉淀 | `experience_sink.record_job_experience`（inbox complete 调） | 预算关键字跳过；**无** timeout/cancel 门禁；默认直接写 `source=system`；无审批；无开关 |
| 记忆 API | GET/POST memory + supersede | 缺 `preview`、`distill-from-item` |
| 内核 UI | processes / mediate / policy / governance / protocol | **无「调度」Tab** |
| 资料卡 | memory tab 列表 | 废止/沉淀/预览按钮不齐 |

**结论**：零件够拼，缺 **两个收口模块 + 全路径挂接 + 可观测 UI + 强制测试**。不是从零发明。

---

## 9. 落地执行手册（按此做完整）

### 9.1 总原则（防范围爆炸）

1. **两个新文件 + 薄挂接**：  
   - `backend/kernel/llm_scheduler.py` → `LlmAdmissionController`（单例）  
   - `backend/kernel/crew_memory.py` → `CrewMemoryAssembler` + `CrewMemoryWriter`  
2. **禁止** 扩 `AgentScheduler` 成 LLM 调度（语义不同：任务队列 vs 模型准入）。  
3. **禁止** 第五套记忆表；Writer 只调 `IdentityRegistry.add_memory` / supersede。  
4. **默认安全**：`crew_memory_auto_distill=false`；日配额默认 0=不限。  
5. 每切片：**单测绿 → 合**；UI 可跟在 API 后 0.5～1 天。

### 9.2 依赖图（严格顺序）

```
S-A LlmAdmissionController
  └─► S-B 挂 llm_round / workforce meta 传 priority
        └─► S-C 日配额 charge 旁路记账
              └─► S-D API status + 内核 Tab「调度」+ 事件
                    └─► S-E 公平/饿死/release 测 + demo

M-A Assembler 抽出 _build_memory_block
  └─► M-B Writer 替换 experience_sink + 失败门禁
        └─► M-C preview / distill API + 资料卡按钮
              └─► M-D（可选）待批 distill
                    └─► M-E chat 路径对齐 Assembler

可并行：Week1 同时开 S-A 与 M-A（无代码冲突）。
```

### 9.3 文件级挂接清单

#### A. LLM 公平调度

| 步骤 | 文件 | 动作 |
|------|------|------|
| 1 | `kernel/llm_scheduler.py` **新建** | `Priority`、`LlmLeaseRequest`、`acquire()`/`release()`、`status()`、超时 watchdog |
| 2 | `kernel/quota.py` **新建**（或并入 llm_scheduler） | 日 key=`YYYY-MM-DD`；global + per-identity 计数；`charge`/`remaining` |
| 3 | `core/config.py` | 增加 §2.3 配置键（默认见上表） |
| 4 | `agent/phases/llm_round.py` | `run_llm_round` 入口 `async with acquire(...)`；`finally` release；usage 后 `quota.charge` |
| 5 | `agent/loop.py` | 调 `run_llm_round` 前把 `source/priority/identity_id` 放进 loop 或 kwargs（从 `_workforce` / session 推断） |
| 6 | `kernel/dispatcher.py` | `_kernel_process_options.meta` 带 `llm_priority`（inbox priority 映射） |
| 7 | `api/routes/kernel.py` | `GET /kernel/scheduler/status`（可选 POST config） |
| 8 | `kernel/domain_events.py`（或现有 emit） | `scheduler.queued/granted/released/rejected` |
| 9 | `frontend/app/kernel/page.tsx` | Tab `sched`：在飞 / 排队 / 配额条；`refetchInterval` 5～8s |
| 10 | `frontend/lib/api.ts` | `getSchedulerStatus()` |
| 11 | `tests/kernel/test_llm_scheduler.py` **新建** | DoD S7 全覆盖 |
| 12 | `tests/...` 门禁 | mock：凡 `llm_service.chat*` 路径须经 acquire（或单元测 in_flight） |

**priority 映射（v1 写死，可配置后置）**：

| 来源 | Priority |
|------|----------|
| 主人 chat（非 workforce） | OWNER_CHAT=100 |
| 主人同步动作 / 提权确认后继续 | INTERACTIVE=80 |
| inbox priority 高 | WORKFORCE_HIGH=50 |
| 默认工单 | WORKFORCE_NORMAL=30 |
| cron / 静默 | BACKGROUND=5 |
| subagent | min(parent, WORKFORCE_NORMAL) |

**acquire 伪代码（必须 finally）**：

```python
lease = await llm_admission.acquire(req)  # 可能 await 排队
try:
    # 现有 stream + charge_tokens
    ...
    llm_admission.charge_quota(identity_id, spent)
finally:
    await llm_admission.release(lease)
```

#### B. 编制记忆

| 步骤 | 文件 | 动作 |
|------|------|------|
| 1 | `kernel/crew_memory.py` **新建** | `build_inject_block` / `maybe_distill_from_job` / `preview` |
| 2 | `kernel/dispatcher.py` | `_build_memory_block` → 委托 `CrewMemoryAssembler`（保留方法签名，测不碎） |
| 3 | `kernel/experience_sink.py` | `record_job_experience` → 薄包装调 Writer；**失败/超时/cancel 不写**；尊重 `auto_distill` |
| 4 | `kernel/inbox.py` | complete 路径传 `status` + 失败标记给 Writer（已调 sink，补全参数） |
| 5 | `api/routes/kernel.py` | `POST .../memory/preview`；`POST .../memory/distill-from-item` |
| 6 | `frontend/components/agents/AgentDrawer.tsx` | 记忆 tab：废止、沉淀按钮 |
| 7 | 派活 UI（若有 Dispatch 面板） | 「预览注入」调 preview |
| 8 | `tests/kernel/test_crew_memory.py` **新建** | DoD M7；旧 `test_memory_authority` / `test_identity_rag_09` 仍绿 |
| 9 | `docs/internal/MEMORY_AUTHORITY.md` | 链 Assembler/Writer + 配置键 |

**Writer 门禁（完整表）**：

| 条件 | 写 experience？ |
|------|-----------------|
| status=done 且结果有效 | 仅当 auto_distill 或手动 distill |
| 结果含 Budget Exceeded / 预算不足 | **否** |
| timeout / cancelled / grounding block | **否** |
| 结果字符 < min_chars | **否** |
| require_approve_distill | 写 proposal 或跳过（v1：手动按钮 + approved_by=user） |
| source=distilled 无 approved_by | **拒绝**（Registry 已有约束则复用） |

### 9.4 「完整」最小可交付（2 周切片，不砍 DoD）

| 日 | 交付 | 勾 DoD |
|----|------|--------|
| D1–2 | S-A + 单测 acquire/release/排队 | 部分 S1/S2 |
| D2–3 | S-B 挂 llm_round；workforce meta | S1 闭环 |
| D3–4 | M-A Assembler + dispatcher 委托 + 旧测绿 | M1/M3 |
| D4–5 | M-B Writer + sink 门禁 + fail 不写测 | M2 |
| D6–7 | S-C 日配额 + charge 旁路 | S3 |
| D7–8 | S-D status API + 内核调度 Tab | S5/S6 |
| D8–9 | M-C preview + distill API + 资料卡按钮 | M4/M5/M6 |
| D10 | S-E/M-E 补测 + demo 脚本 4 步 | S7/M7 + 联合 |
| 缓冲 | 主人优先调优、wait_boost、文档 | 产品清单 §6 |

**可砍到下一迭代（仍算「产品完整」若默认关）**：

- M-D 待批 evolution proposal（手动 distill 已闭环）  
- soft-cancel 后台 lease（v1 只「下一空槽给主人」）  
- chat 联系 TA 全量对齐（M-E 可并入缓冲，workforce 已完整）

### 9.5 演示脚本（验收用，5 分钟）

路径：`examples/demo_fair_sched_memory/run.py`（或 `backend/scripts/demo_fair_sched_memory.py`）

```
1. 设 llm_max_in_flight=1，投 2 个假工单 + 1 次主人 chat 流
   → GET /kernel/scheduler/status 见 queue≥1，主人 granted 优先
2. 员工写入 persona/duty + 10 条 experience
   → POST memory/preview → experience 条数 ≤ crew_memory_experience_max_inject
3. 极小 token_budget 跑工单至 Budget Exceeded
   → current_memory experience 条数不变
4. POST distill-from-item（done 工单）
   → 新 experience 出现；supersede 后 current 消失
```

### 9.6 验收签字（产品语言，对应 §6）

- [ ] 内核页「调度」能看见在飞 / 排队 / 配额  
- [ ] 主人发消息时后台会让槽（status 可证）  
- [ ] 资料卡可废止记忆；可预览注入；可手动沉淀  
- [ ] 失败工单不沉淀  
- [ ] demo 4 步可复现  

---

## 10. 与「AIOS 差距」的关系（为什么这两项要完整）

| 能力 | 对外一句话 | 完成这两项后 |
|------|------------|--------------|
| LLM 公平调度 | 编制不是抢锁跑模型，是可解释的资源 OS | 可 demo「主人优先 + 不饿死」 |
| 编制记忆 | 员工有人设，失败不教坏 | 可 demo「preview + 失败不写」 |

**不做完整的后果**：继续只有工单并发 + 半截 experience_sink → 社区只能看到「多 agent 聊天」，看不到 OS 感。

---

*方案完。实施按 §9 文件清单开 PR；每切片带测；勾 §0 DoD 与 §9.6 即视为两项完整。*
