# Run 统一实体（Phase 2.1）

> 一页纸。拍板日期：2026-07-30 · 实现策略：**演进 `agent_runs`，不平行新建 `runs` 表**

---

## 1. 一句话

**一切执行都是 Run。** Identity 是执行者；Cluster / SubAgent / Hire 是 Identity 的编排形态；Workflow 是 Run 模板。

---

## 2. 权威表：`agent_runs`（对外仍称 Run）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID PK | Run id |
| `origin` | str index | `chat` \| `inbox` \| `cron` \| `cluster` \| `subagent` \| `headless` |
| `status` | str index | **保留细粒度** phase SM（见 §3） |
| `session_id` | FK sessions | 必填（无会话入口用系统/合成 session 或专用） |
| `user_id` | FK? | 可选 |
| `identity_id` | UUID? | 编制员工；chat 主会话可空 |
| `parent_run_id` | UUID? self-FK | 子 Run（subagent / cluster 子任务） |
| `mode` | str | loop 模式（default/workforce/subagent…）— 与 origin 正交 |
| `checkpoint` | JSON? | **权威将迁此列**（2.3）；2.1 可写镜像，session.config 仍可读 |
| `token_limit` | int | 预算上限（0=未设） |
| `token_used` | int | 已用 |
| `input_summary` / `final_summary` / `error` | | 列表展示 |
| `total_iterations` / `total_tool_calls` | int | 统计 |
| `started_at` / `ended_at` | datetime? | ended_at = finished_at |
| `meta` | JSON? | 扩展（inbox_item_id、cluster_run_id 链接等） |
| `created_at` / `updated_at` | | TimestampMixin |

**不另起 `runs` 表**：避免双写竞态；Python 侧 `AgentRun` 即 Run，API 文档写 Run。

---

## 3. 状态机（两层）

### 3.1 存储层（细粒度，已有 `run_state.py`）

```
created → planning → executing ⇄ waiting → verifying → done|failed|cancelled
```

2.1 **不改**合法迁移表；`run_lifecycle.transition` 唯一入口调用 `validate_transition`。

### 3.2 对外粗粒度（API / UI 映射，只读）

| 对外 | 内部 status |
|------|-------------|
| `pending` | `created` |
| `running` | `planning` \| `executing` \| `verifying` |
| `waiting_approval` | `waiting` |
| `suspended` | （2.3 引入 `interrupted` 后映射） |
| `done` / `failed` / `cancelled` | 同名 |

---

## 4. origin 推断（2.1 默认）

| 信号 | origin |
|------|--------|
| `mode == "workforce"` 或 meta.inbox_item_id | `inbox` |
| `mode == "subagent"` 或 parent_run_id | `subagent` |
| meta.origin 显式 | 用显式值 |
| meta.cluster_run_id | `cluster` |
| meta.source == "cron" | `cron` |
| mode headless / agent_key 特殊 | `headless` |
| 默认 | `chat` |

2.2 起：四路径**创建时必传** origin，禁止靠猜。

---

## 5. 与并行实体关系

| 实体 | 关系 |
|------|------|
| **ClusterRun** | 2.2：创建 cluster 时同时建 origin=cluster 的父 AgentRun；`meta.cluster_run_id` 互链；渐进取代平行心智 |
| **Kernel Process** | process.meta.run_id 指向 AgentRun；process 是运行时句柄，Run 是权威记录 |
| **InboxItem** | 工单 claim 后创建 origin=inbox 的 Run；item.meta.run_id |
| **session.config checkpoint** | 2.1 双写 Run.checkpoint；2.3 读权威切到 Run 列 |
| **CronExecutionLog** | 仍记 tick；业务执行必须落 Run |

---

## 6. 模块边界

| 模块 | 职责 |
|------|------|
| `run_state.py` | 细粒度 SM 合法表（纯函数） |
| `run_lifecycle.py` | **唯一**写 status 入口；origin 推断；对外 status 映射；interrupted 标记（2.3） |
| `run_recorder.py` | loop 胶水：create + lifecycle.transition + steps + EventBus |
| `AsyncAgentRunRepository` | 持久化（可 alias `AsyncRunRepository`） |
| 禁止 | loop/dispatcher 直接 `update_run(status=…)` 绕过 lifecycle |

---

## 7. 切片与不做

**2.1 本切片**：列 + 迁移 + lifecycle + recorder origin + GET /runs?origin= + 测试。✅  
**2.2 本切片**：四路径显式 origin + process/inbox 互链 + cluster 父/子 Run。✅  

**明确延后**：
- kill-9 自动续跑（2.3）
- checkpoint 权威单写 Run 列（2.3）
- 状态词汇彻底改名（避免大爆炸）
- cluster 子任务从「纯 LLM」升级为完整 loop Run（可选）

---

## 8. 验收

### 2.1
1. 新 Run 必有 `origin`；旧行 backfill 合理默认。  
2. `GET /runs?origin=inbox` 只返回 inbox。  
3. recorder 创建路径带 identity_id / parent_run_id 时落列而非仅 meta。  
4. 既有 `test_durable_run` 全绿。  

### 2.2
1. chat → origin=chat；inbox → origin=inbox；cron 投递 → origin=cron。  
2. process.meta.run_id + inbox.payload.run_id 可互查。  
3. cluster 启动建父 Run；结束写子 Run（parent_run_id）与父终态。  
