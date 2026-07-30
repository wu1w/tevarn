# 执行模型（Phase 2.4）

> 一页纸。讲不清 = 没收敛完。

---

## 一句话

**一切执行都是 Run；Identity 是执行者；Cluster / SubAgent / Hire 是 Identity 的三种编排形态；Workflow 是 Run 模板。**

---

## 对象关系

```
                    ┌─────────────┐
                    │  Identity   │  编制员工（权限/预算/记忆）
                    └──────┬──────┘
                           │ 执行
                           ▼
┌──────────┐  创建   ┌─────────────┐  parent_run_id  ┌────────────┐
│  Trigger │ ─────► │     Run     │ ◄─────────────── │ Child Run  │
│ chat/    │        │ agent_runs  │                  │ subagent/  │
│ inbox/   │        │ origin=…    │                  │ cluster_sub│
│ cron/    │        │ checkpoint  │                  └────────────┘
│ cluster  │        └──────┬──────┘
└──────────┘               │
                           │ 可选
                           ▼
                    ┌─────────────┐
                    │Kernel Process│ 运行时句柄（mediate/budget）
                    │ meta.run_id  │
                    └─────────────┘
```

| 概念 | 是什么 | 不是什么 |
|------|--------|----------|
| **Run** | 一次可恢复的执行记录（权威在 `agent_runs`） | 不是聊天消息本身 |
| **Identity** | 谁在干活（capabilities + memory） | 不是一次任务 |
| **Cluster** | 多子任务编排；父 Run + 子 Run + ClusterRun 互链 | 不是独立心智 |
| **SubAgent** | 嵌套 Run（parent_run_id） | 不是临时无档案进程（已禁用闷跑） |
| **Hire** | 创建 Identity 并入编制 | 不是 Run |
| **Workflow** | DAG 模板 → 父 Run + 节点子 Run | 不是第二套状态机 |
| **Goal** | 挂在 Run 上的 todo 进度（`goal.run_id`） | 不是独立执行轨 |
| **InboxItem** | 工单信封；payload.run_id 指向执行 | 不是 Run 本体 |

---

## 四条触发路径 → origin

| 触发 | origin | 入口 |
|------|--------|------|
| WebSocket 聊天 | `chat` | `websocket._run_agent_safe` |
| 编制工单 | `inbox` | `dispatcher` + `mode=workforce` |
| 定时派活 | `cron` | `cron_scheduler` → inbox → dispatcher |
| 集群 | `cluster` | `cluster` 路由建父 Run，子任务落子 Run |
| 迷你委派 | `subagent` | `run_subagent` |

统一列表：`GET /runs`、`GET /runs?origin=`。

---

## Durable（2.3）

1. **Checkpoint 权威**：`AgentRun.checkpoint`（session.config 双写兼容）。  
2. **启动恢复**：非终态 → `interrupted`；`agent_run_auto_recover` 时 inbox/cron/headless 自动 `resume_session_agent`。  
3. **chat**：只标记 interrupted，用户续聊 / API resume。  

---

## 状态（两层）

- **存储**：created → planning → executing ⇄ waiting → verifying → done|failed|cancelled；另有 **interrupted**。  
- **对外**：pending / running / waiting_approval / suspended / done / failed / cancelled（`public_status()`）。  

唯一写 status 业务入口：`run_lifecycle` / `RunRecorder.transition`。

---

## 模块地图

| 模块 | 职责 |
|------|------|
| `loop.py` + `loop_io` / `loop_cluster` / `loop_tools` / `phases/*` | 主循环（已 phases 化，loop 本体 <1500 行） |
| `run_recorder` / `run_lifecycle` / `run_recovery` | Run 写路径与恢复 |
| `checkpoint` / `resume` | 断点与续跑提示词 |
| `dispatcher` / `inbox` | 编制唤醒 |
| `cluster_executor` + routes | 集群编排 |
| `workflow_engine` | DAG 模板 → Run 树 |

---

## 禁止

- 再发明第五种「执行心智」而不建 Run  
- 绕过 lifecycle 直接改 status  
- 把 ClusterRun / InboxItem 当成与 Run 平行的权威执行记录  
