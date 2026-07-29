# Takton Alpha AIOS — 2026-07-29 会话改动清单

> 打包日：2026-07-29  
> 范围：本会话在 `takton-alpha-aios-0.6-sprint-20260729-0711` 上的加固与运行时修复  
> 默认策略：`TAKTON_GROUNDING_MODE=soft`（软提示为主，硬拦为辅）

---

## 一、目标与背景

1. **多类幻觉防护**（不只代码路径）：指标、CVE、堆栈、「最新」、绝对断定、毒工单级联等。  
2. **平衡强模型能力**：减少硬流程/长 system prompt 对 Claude/GPT/Grok/Kimi/GLM 的束缚。  
3. **审计员工单反复「失败」根因治理**：30k 预算过紧、会话历史膨胀、Budget 结果误标 `done`、experience 污染。

---

## 二、新增文件

| 路径 | 作用 |
|------|------|
| `backend/agent/dispatch_grounding.py` | CEO→员工 **派单扫描**：路径/模板模块/硬指标/CVE/堆栈/API/版本等；block/warn；worker 卫生块；CEO 只派单策略 |
| `backend/agent/grounding_policy.py` | **soft / balanced / strict** 强度策略；强模型自动降一档；弱模型 soft→balanced |
| `backend/agent/workforce_budget.py` | 编制工单 **按任务类自动抬 token 预算**（审计≥120k 等），不改档案默认值 |
| `backend/tests/test_dispatch_grounding.py` | 派单门 / soft 策略相关测试 |
| `backend/tests/test_workforce_budget.py` | 自动抬预算与 Budget 文案识别测试 |
| `reports/CHANGELOG_2026-07-29_session.md` | 本清单 |

（若仓库中已有 `task_grounding.py` / `completion_gate.py` 等，本会话做了大幅扩展，见下节。）

---

## 三、主要修改文件

### 3.1 落地 / 幻觉（Grounding）

| 文件 | 改动要点 |
|------|----------|
| `backend/agent/task_grounding.py` | 多类目分类与完成校验；**soft 下仅硬拦空工具/fix·build 无写**；浅层工具改为 soft 放行；**缩短** `grounding_prompt_block`；报告脚注（路径/检索/统计/断定等） |
| `backend/agent/completion_gate.py` | 接入 policy；派单优先仅在 hard 策略下打断；软提示文案 |
| `backend/agent/system_prompt.py` | 注入 `grounding_prompt_block()`（短版 Evidence） |
| `backend/agent/phases/epilogue.py` | 终答附加落地校验脚注（`maybe_annotate_report`） |
| `backend/agent/phases/no_tool_round.py` | completion followup 状态文案改为「补充取证」；可传 model_name |
| `backend/agent/audit_grounding.py` | 兼容 shim → `task_grounding` |
| `backend/tools/builtins/crew_steward_tools.py` | `assign` / `requeue` 前 `scan_dispatch_instruction`；block 拒单；warn 软前缀；`force=true` 逃生 |
| `backend/kernel/dispatcher.py` | worker 卫生 + 任务类轻提示；**自动抬预算**；**Budget→failed**；**记忆瘦身**；「已挂载工具」提示 |

### 3.2 编制预算 / 会话 / 失败语义

| 文件 | 改动要点 |
|------|----------|
| `backend/kernel/dispatcher.py` | `_effective_budget(ident, instruction)`；`_finish_item` 区分 budget fail；`_build_memory_block` 少灌 experience；`_workforce_skip_history` |
| `backend/kernel/inbox.py` | `fail(..., result=, terminal=)`：预算类 **terminal failed**，不无限自动重试 |
| `backend/kernel/experience_sink.py` | Budget 失败 **不写** experience，防下一单污染 |
| `backend/agent/loop.py` | `mode=workforce` / `_workforce_skip_history` 时 **不加载历史对话**（治首包 +33k 顶穿 30k） |

### 3.3 测试

| 文件 | 说明 |
|------|------|
| `backend/tests/test_dispatch_grounding.py` | soft 默认：路径/模板 block；指标/CVE/堆栈 warn；CEO 只派单 soft 放行 |
| `backend/tests/test_task_grounding.py` | soft 下浅层工具放行等 |
| `backend/tests/test_audit_grounding.py` | 浅层 glob soft 放行；strict 仍可硬拦 |
| `backend/tests/test_workforce_budget.py` | 审计抬到 ≥120k 等 |
| `backend/tests/kernel/test_workforce_06.py` | 审计工单进程预算抬升断言 |

---

## 四、行为变化摘要（给使用者）

### 4.1 派单（crew_steward assign）

- **硬拒（block）**：空指令、**不存在的路径**、常见**模板模块名**（如 `orchestrator.py` / `session_manager.py`）。  
- **软提示（warn 仍派单）**：硬百分比/漏洞数、多 CVE、堆栈、「最新」、API 路由等（soft 默认）。  
- `force=true` 可跳过 block。  
- `requeue` 同样过校验。

### 4.2 完成校验（completion_gate）

- **soft**：零工具落地任务轻提示 1 次；fix/build 无写仍拦。  
- 不再用长清单 + 多轮 ritual 绑死强模型。  
- 报告后脚注仍可能标「路径不存在 / 无 web 等」。

### 4.3 环境变量

| 变量 | 含义 | 默认 |
|------|------|------|
| `TAKTON_GROUNDING_MODE` | `soft` / `balanced` / `strict` | `soft` |
| `TAKTON_BUDGET_LIFT_MULT` | 预算抬升倍数 | `1` |
| `TAKTON_WORKFORCE_BUDGET_HARD_CAP` | 单进程预算硬顶 | `500000` |
| `TAKTON_WORKFORCE_FALLBACK_BUDGET` | 身份无默认时的兜底 | `50000` |

### 4.4 自动抬预算（进程级，不改 DB 档案）

| 任务/角色 | 地板（约） |
|-----------|------------|
| 审计 / 角色含「审计」 | 120_000 |
| 排查 | 100_000 |
| 检索 / 统计 / 对比 / 修 bug / 建包 | 80_000 |
| 文档 / 出处 | 60_000 |
| 普通闲聊 | 保持身份默认 |

### 4.5 Budget Exceeded

- 工单记 **`failed`**（非 `done`），带 result 文案。  
- 不自动用同一低预算死循环重试（`terminal=True`）。  
- 不沉淀为 experience。

### 4.6 会话瘦身

- 编制工单跳过历史消息加载。  
- 身份记忆：persona/duty 为主；experience 最多 1 条且截断。

---

## 五、未改 / 已知边界

- 不自动批量改库里员工 `default_token_budget` 字段（进程侧动态抬）。  
- 向量 RAG seed 索引在无 Embedding/Qdrant 时仍会 fail（环境原有问题，非本会话引入）。  
- 模型若仍拒用工具，依赖短提示 + 预算，无法 100% 消除模型自身拒答。  
- `force=true` 派毒工单仍可能污染员工——仅主人/明确要求时使用。

---

## 六、建议验证步骤

1. 重启 kernel：`python -m backend.runtime --host 127.0.0.1 --port 8090`  
2. `pytest backend/tests/test_workforce_budget.py backend/tests/test_dispatch_grounding.py backend/tests/test_task_grounding.py -q`  
3. 派一单「安全审计 + 真实路径」：进程 budget 应 ≥120k；工单正常 `done`。  
4. 故意 assign 假路径：应 block。  
5. 若仍见 Budget：侧栏应为 **failed**，而非 done。

---

## 七、相关历史上下文（本会话前半）

同项目本轮/前轮还涉及（若包内代码已存在则一并交付）：

- 预算抽屉空数据修复、charge_tokens / 流式 usage 估计  
- 10 项 harness 对比实现（权限 DSL、dangerous_paths、process identity、experience_sink、plan_session 等）  
- goal 写穿、向量索引队列  
- CEO 动态 cap 授予（grant_store / cap_requests）  
- 产品 triad employee/job/approval 等  

细节以仓库当前源码与既有 `CHANGELOG.md` / `PLAN.md` 为准；**本日会话增量以本文 + 上表路径为准**。
