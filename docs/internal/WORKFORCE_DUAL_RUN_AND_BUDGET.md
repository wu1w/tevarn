# 编制双 Run / 预算问题：汇总与方案

> 2026-07-30 日志复盘（多员工并行审计大任务）后落地。

---

## 1. 问题汇总

| # | 问题 | 严重度 | 现象 |
|---|------|--------|------|
| **P1** | **同身份双 Run / 双进程** | 高 | backend-engineer 同时 `verifying` + 新 `executing`；kernel 两条 `wf:{id}` running |
| **P2** | 预算顶到红线 | 高 | 进程 ~290k–296k / 400k，再几轮可能 BudgetExceeded |
| **P3** | Run 观测假零 | 中 | `total_iterations=0`、`token_used=0`，但 steps 与 process.tokens_used 很大 |
| **P4** | 日志停更 vs 进程仍 running | 中 | 可能卡在 LLM；观测困难 |
| **P5** | 日志双写 / 401 噪音 | 低 | 双 handler；旧 token |

---

## 2. 根因（双 Run）

1. **claim 只看内存 `_busy`**，不看 DB 已有 `claimed` 同行身份 → 多 worker 或 busy 丢失时双派。  
2. **新工单开工前不清理** 同 `wf:{identity}` 残留非终态进程（上一单 end 失败 / verifying 悬挂）。  
3. **合法「上一单刚结束 → 下一单」** 若上一进程未 kill，会叠第二条。

编制约定：**一员工同时只干一单**（`agent_dispatcher_max_identity_concurrent=1`）。

---

## 3. 已落地修复（代码）

### 3.1 防双 Run（必做，已实现）

| 层 | 改动 |
|----|------|
| `inbox.claim_next` | 跳过 **DB 中已有 status=claimed** 的 identity |
| `dispatcher._run_item` | 开工前 `retire_live_identity_processes(wf:{id})` |
| `loop.create_process` | 编制路径再清一次同 identity 残留 |
| `kernel` | 新增 `live_processes_for_identity` / `retire_live_identity_processes` |
| 测试 | `test_workforce_dual_run_and_budget.py` |

### 3.2 CEO 动态追加预算（已实现）

**可以。** 任务过程中由 CEO 给**正在跑的 kernel 进程**加 `token_budget` 上限，**不重置已用额度**。

| API | 作用 |
|-----|------|
| `POST /api/kernel/processes/{id}/budget/top-up` | `{ "amount": 100000, "reason": "审计加长" }` |
| `POST /api/kernel/identities/{id}/budget/top-up-running` | 该员工所有 running 进程各 +amount；`also_default: true` 同时抬档案默认预算 |

实现：`kernel.top_up_budget` → 写 process.token_budget + 审计 `budget_top_up` + 可选同步 `AgentRun.token_limit`。

下一刀 `charge_tokens` 立即用新上限。

### 3.3 Run 指标假零（已修一部分）

- `RunRecorder.bump_iteration` / `set_token_used`，finish 时写入 `total_iterations` + `token_used`。

---

## 4. 运营建议（当前卡住的大任务）

1. **停掉重复 backend 进程**（Kernel 页或 stop job），只留一条。  
2. **需要继续时 CEO 加预算**，例如：  
   `POST .../processes/{pid}/budget/top-up` body `{"amount": 200000, "reason": "完成审计报告"}`  
3. 加预算后若仍无日志增长，视为 LLM 挂死 → stop + 一键续跑。  
4. 后续大任务：CEO 派单时在 payload 写更大 `token_budget`，或档案 `default_token_budget` 提高。

---

## 5. 未做 / 后续

- UI 一键「加预算」按钮（API 已有）  
- 多 worker 下 Redis 分布式 busy 锁（claim 的 DB claimed 检查已覆盖主路径）  
- 日志双 handler 根治  
