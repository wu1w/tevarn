# Agent Kernel 规划与路线

> alpha 私有线 · v0.4.6-alpha
> 本文档随 kernel 演进持续更新。

## 现状（Alpha）

Kernel 作为观测层 + 拦截层接入 Loop：

- **进程模型**：每个 run/工单一个 kernel process（capabilities、token_budget、meta）
- **拦截**：工具调用经 `kernel.mediate()`（权限/HMAC 签名/能力检查）
- **预算**：loop 在 LLM 调用后主动 `charge_tokens`，超限 raise → run 硬中断
- **Workforce**：identity registry + inbox + dispatcher 异步唤醒 + evolution engine
- **异步兜底预算**：身份未设预算时挂 `agent_workforce_fallback_budget`（默认 50000）

## Alpha Review 处置（2026-07-27 外部评审四条）

### #1 Kernel↔Loop「搭桥非融合」—— ✅ Phase 2 已落地（2026-07-27）

- **事前预算检查**：每轮 iteration 开头 gate 预估下次 LLM 调用消耗
  （近期上下文 /3.4 + 输出预留），剩余不足即事前中断——
  不再只靠 llm_round 的事后 charge 兜底
- **suspend/resume**：process 状态机 + asyncio.Event 同步原语，
  kernel.suspend_process/resume_process，loop gate 挂起阻塞等待恢复
  （轮询响应 stop，不死等）
- **调度节奏**：gate 内 asyncio.sleep(0) 公平让出语义点
  （session lock 管同会话互斥，gate 管跨 run 仲裁）
- 开关：agent_kernel_budget_precheck / agent_kernel_precheck_reserve

### #2 Dispatcher 每工单新建 Loop —— ✅ Worker 池已落地（2026-07-27）

per-identity 长生命周期 loop 实例池（`_worker_for`/`evict_worker`）：
repo 引用 / RAG 懒加载缓存 / context_manager 跨工单复用。
安全性：busy_identity_ids 同身份串行 + `loop._reset_run_state()`
每单前显式归零 run 级状态（防跨工单泄漏）。

### #3 Evolution 阈值硬编码 —— ✅ 已修复（2026-07-27）

6 阈值全部参数化进 settings（`agent_evolution_*`，默认值与 alpha 常量一致），
`evolution_engine._threshold()` 读配置、模块常量仅兜底。
研发型/运营型身份可按需调整。

### #4 Identity Memory 与 RAG/Wiki/memory_graph 断开 —— ✅ 已落地（2026-07-27）

identity memory 条目向量化纳入 RAG 检索（identity_memory collection）：

- **写入挂钩**：add_memory 索引；supersede 清旧向量 + 索引新版
  （版本链同步——检索命中已取代记忆 = 事故）。best-effort：
  本地模式/索引失败不阻塞记忆写入
- **prompt 注入**：条目 ≤ 阈值（8，可调）全量硬注入（人格/职责常驻）；
  超阈值按工单相关性检索 top-k，检索不可用回落全量截断
- **执行期召回**：workforce 模式的 `_inject_rag_context` 附带
  按当前输入检索身份记忆 top-3——中期任务上下文漂移后，
  相关经验/方法论仍能浮现

## 多 worker 状态外部化（0.4.x 落地）

### 观测面（DB，默认开）
- **进程档案** `kernel_processes`：create/end 经 sink 落盘；`GET /kernel/processes` 在
  `agent_kernel_shared_state=true` 时合并 DB + 本 worker 内存（内存优先）
- **提权** `kernel_escalations`：request/approve/deny 经 sink 落盘；启动 recover 注回
  pending；list API 合并 DB
- **事件** 仍以 JSONL audit_store 为跨重启权威；内存环形缓冲是本进程热视图

### 执行面（Redis，可选）
配置：
```
agent_kernel_redis_shared=true
redis_url=redis://127.0.0.1:6379/0
```
- 模块：`backend/kernel/shared_store.py`（**同步** redis-py，符合 mediate 零 await 红线）
- `create_process` / 能力变更 / end → `HSET` 进程 Hash
- `mediate` / `charge_tokens` / `get_process` → 本地缓存未命中时从 Redis 水合
- `charge_tokens` → `HINCRBY` 原子扣减
- 提权 pending → Redis Set + Hash；`ensure_escalation_loaded` 优先 Redis
- 跨 worker suspend/resume：state 写 Redis；loop `wait_if_suspended` 轮询刷新

未配置 / redis 包缺失 / ping 失败 → 静默回退单进程内存（与历史行为一致）。

## 打磨方向（不阻塞 alpha 试用）

1. **三设备实机验证**：Mi310p Arch、Mac、Win Xeon——
   上传工单 → 主 agent 审批 → dispatcher 唤醒 → 报告落库可见
2. **前端 Workforce 页面**：身份 CRUD、工单列表/上传、报告时间线、
   演化建议审批面板
3. **阶段 4（治理 UI）**：预算面板、kernel 监控页（/proc 风格）
4. **身份能力 UI 声明**：workforce 页创建身份时勾选能力
5. **优化源码移植**：外部优化代码逐个评估移植
   （规则：先跑测试确认行为变化，看不懂的不合）
