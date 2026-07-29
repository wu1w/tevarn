# Backend/Agent 目录巡检报告

> 巡检时间：2026-07-28 21:44 CST  
> 巡检范围：`backend/agent/` 目录下所有 `.py` 文件

---

## 1. 文件清单与行数

| 文件 | 行数 | 职责简述 |
|------|------|----------|
| `__init__.py` | 5 | 导出 NexusAgentLoop |
| `_takton_paths.py` | 15 | 本地数据目录 |
| `agent_contract.py` | 246 | 交付契约 + 复核循环 |
| `auto_classify.py` | 395 | 自动模式权限分类（TOML 规则） |
| `best_of_n.py` | 74 | best-of-n 评分 + fanout |
| `checkpoint.py` | 80 | 分段 checkpoint（触顶续跑/崩溃恢复） |
| `cluster_aggregator.py` | 360 | 集群结果聚合（vote/merge/synthesize） |
| `cluster_executor.py` | 624 | 真·并行子代理执行器 |
| `cluster_protocol.py` | 445 | JSON 任务分发协议 |
| `command_classifier.py` | 184 | Shell 命令语义分类（read/write/mixed） |
| `completion_gate.py` | 160 | 完成门控（needsFollowUp 判断） |
| `context.py` | 452 | ContextManager（CtxItem 5 层上下文构建） |
| `context_compress.py` | 138 | 上下文压缩（history 折叠） |
| `context_engine.py` | 88 | 可插拔上下文引擎（token 计量） |
| `context_pipeline.py` | 670 | 上下文 pipeline（L1/L3/L5 分层注入） |
| `decisive.py` | 156 | 犹豫检测（减少单工具轮次） |
| `doom_loop.py` | 76 | 死循环检测（重复工具 + 相似参数） |
| `goal_state.py` | 375 | Goal 状态管理（Todo 树 + autopilot） |
| `iteration_budget.py` | 51 | 单 run 迭代预算 |
| `loop.py` | **2663** | ⚠️ **核心循环（NexusAgentLoop）** |
| `loop_base.py` | 67 | AgentLoopBase（stop flag + ports） |
| `robust.py` | 202 | 稳健性工具（重试/续跑/瞬态错误） |
| `run_state.py` | 67 | 状态机（RunStatus 枚举 + 迁移表） |
| `system_prompt.py` | 394 | 系统 prompt 组装 |
| `tool_policy.py` | 645 | 工具/注入策略（场景预判） |
| `tool_result_contract.py` | 140 | 工具结果规范化 + 截断 |
| `tool_status.py` | 63 | UI 工具状态行 |
| `turn_retry.py` | 146 | Turn 级重试分类 |
| `workforce_dispatch.py` | 149 | 编制派活（Identity → Inbox） |
| `working_mode.py` | 297 | 工作方式/权限体系单一事实源 |
| `workspace_contract.py` | 100 | Workspace 契约文件注入 |
| `run_state.py` | 67 | 状态机 |

**合计：约 31 个 .py 文件（不含 `phases/` 子目录），约 10,000+ 行**

---

## 2. TODO / FIXME / HACK 注释

**⚠️ 未发现传统 TODO/FIXME/HACK 注释。**

唯一匹配项在 `system_prompt.py:110`，但那是 prompt 模板中的文字内容（"temporary TODO state to memory"），不是代码注释。

结论：代码库维护良好，技术债务通过正式 Issue/工单追踪而非 inline TODO。

---

## 3. 超过 100 行的函数

**最大函数：`NexusAgentLoop._run_locked()`（约 700+ 行，从 L805 到 L1508+）**

该函数包含了 Agent Loop 的全部核心逻辑：
- 保存用户消息 + TTL 清理
- 加载历史消息
- 场景预判
- 组装 messages（CtxItem + skill 注入）
- Auto Optimize
- 上下文压缩
- RAG + Wiki + 实体注入
- Goal 模式初始化
- 集群模式准备
- **主循环（LLM 调用 + 工具执行）**
- 最终回复保存
- ContextFlow 记录

⚠️ 这是**单函数过长**的典型反模式。虽然已用 phases/ 做了逻辑拆分（llm_round、tool_round、prologue、cluster_mode），但 `_run_locked` 仍承担了过多编排职责。

其他较重函数（估计 >100 行）：
- `_run_inner()`（L701-L804）：进程创建 + 锁管理 + 生命周期
- `_execute_registered_tool()`（L220-L297）：工具执行入口（kernel 中介 + 重复搜索 + 契约拦截）
- `_inject_rag_context()`：RAG 注入 + 身份记忆召回

---

## 4. 状态机实现（loop.py）

### RunStatus（`run_state.py`）

```python
class RunStatus(str, PyEnum):
    CREATED = "created"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING = "waiting"
    VERIFYING = "verifying"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

**迁移表**（`TRANSITIONS` 字典）：
- `CREATED` → {PLANNING, EXECUTING, DONE, FAILED, CANCELLED}
- `PLANNING` → {EXECUTING, WAITING, VERIFYING, DONE, FAILED, CANCELLED}
- `EXECUTING` → {WAITING, VERIFYING, DONE, FAILED, CANCELLED}
- `WAITING` → {EXECUTING, DONE, FAILED, CANCELLED}（等待后可回到执行）
- `VERIFYING` → {EXECUTING, DONE, FAILED, CANCELLED}
- 终态 DONE/FAILED/CANCELLED → 空集（不可迁移）

✅ 实现规范：`can_transition()` + `validate_transition()` + `IllegalTransitionError`。

### `_reset_run_state()`（L166-L187）

WorkforceWorker 池复用前重置 run 级状态：
- `_kernel_process` / `_kernel_process_options`：进程归属
- `_run_recorder`：durable run 记录器
- `_search_fp_counter`：重复搜索计数器
- `_contract_wl_*`：契约白名单
- `_should_stop`：停止信号
- `_llm_fail_streak`：LLM 失败连续计数
- `_reactive_compact_used`：reactive compact 标记

✅ 设计合理：显式归零防止 run 级状态跨工单泄漏。

---

## 5. 概念并存检查（G1 缺口）

### Identity

✅ **已存在**。`workforce_dispatch.py` 中通过 `find_identity_by_name_or_id()` 操作 Identity（从 `kernel.identity_registry` 获取）。Identity 是 Workforce 体系的员工身份实体。

### SubAgent

✅ **已存在，但用法混合**：
- `cluster_executor.py` 中 `_sub_agents: dict[str, Any]` —— 真·并行子代理（asyncio.gather）
- `loop.py` 中 `_subagent_depth` —— 嵌套深度控制（delegate_task 防失控）
- `workforce_dispatch.py` 中明确**禁止** `manage_sub_agent create` 假装并行

⚠️ **SubAgent 概念与 Identity/Workforce 并存**，但有明确的迁移方向：
- 旧路径：`manage_sub_agent`（临时子进程，无工单账本）
- 新路径：`workforce_dispatch` → Identity + Inbox + 工单追踪

### Cluster

✅ **已存在**。独立的集群子系统：
- `cluster_executor.py`（624 行）：真·并行执行器
- `cluster_aggregator.py`（360 行）：结果聚合
- `cluster_protocol.py`（445 行）：JSON 任务分发协议
- `phases/cluster_mode.py`：集成入口

Cluster 是**多子代理并行**的执行框架，与 Identity/Workforce 是不同层级的概念（Cluster 是执行机制，Identity 是身份体系）。

### Workflow

❌ **未在 `backend/agent/` 中定义 Workflow 类**。但有相关概念：
- `goal_state.py`（375 行）：Goal 状态管理（Todo 树 + autopilot），是轻量级工作流
- `checkpoint.py`（80 行）：分段 checkpoint，支持续跑
- `phases/` 目录中的 prologue/tool_round/llm_round 是流程阶段

⚠️ **G1 缺口确认**：Workflow（多步骤编排）目前由 Goal + Checkpoint + phases 承载，但没有统一的 `Workflow` 抽象类。

---

## 6. 总结

| 维度 | 状态 | 说明 |
|------|------|------|
| 文件组织 | ✅ 良好 | 31 文件，职责清晰，phases/ 子目录拆分 |
| TODO/FIXME | ✅ 干净 | 无 inline 技术债务标记 |
| 大函数 | ⚠️ 需关注 | `_run_locked()` ~700 行，已用 phases/ 拆分但仍偏重 |
| 状态机 | ✅ 完善 | 8 状态 + 迁移表 + 校验 + reset |
| Identity | ✅ 存在 | Workforce 身份体系，kernel.identity_registry |
| SubAgent | ⚠️ 混合 | 旧 manage_sub_agent 与新 workforce 并存，有迁移方向 |
| Cluster | ✅ 独立 | 完整的并行执行框架（executor/aggregator/protocol） |
| Workflow | ❌ 缺失 | 无统一 Workflow 抽象，由 Goal+Checkpoint+phases 承载 |

### 建议优先级

1. **🔴 拆分 `_run_locked()`**：将编排逻辑进一步下沉到 phases/，目标 <300 行
2. **🟡 统一 Workflow 抽象**：为 Goal/Checkpoint/phases 提供统一的多步骤编排接口
3. **🟡 完成 SubAgent → Workforce 迁移**：清理 manage_sub_agent 旧路径
