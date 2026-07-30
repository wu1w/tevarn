# Takton 自审核实 + 补查 + 修复计划（2026-07-30）

> 来源：编制四员工 v4 审计汇总 + 人工补查（项目 `.venv`）+ 近期运维问题（双 Run / 预算 / 日志 / busy）

---

## 一、补查结果（qa 未完成部分）

### A. API 路由注册

| 指标 | 结果 |
|------|------|
| `backend/api/routes/*.py`（除 `__init__`） | **47** |
| `__init__.py` block import | **46**（+ `health` 函数内导入） |
| `include_router(...)` | **48**（含 `cluster` HTTP+WS、`health`） |
| 未注册路由文件 | **无** |
| 幽灵 import | **无** |

**结论**：路由注册完整，与 `architecture-audit-v4.md` 一致。qa 预算爆了但该结论后来由架构扫描补上了。

### B. 测试套件

| 指标 | 结果 |
|------|------|
| `backend/tests/**/*.py` | **143** |
| `test_*.py` | **137** |
| 根目录 `tests/` | **0**（已收束，正常） |
| `pytest --co`（项目 venv） | **可收集，exit 0** |

### C. TODO / FIXME 扫描（backend `*.py`，含注释）

| 指标 | 结果 |
|------|------|
| 命中总数 | **4** |
| 业务代码 | `system_prompt.py` 1 处（prompt 文案 “temporary TODO state”，非未实现） |
| 测试断言 | `test_stub_fill_p0p2.py` 3 处（断言源码**不得**含 TODO） |

**结论**：业务层几乎无真实 TODO 债；自审「0 TODO」对 Services/Kernel 清单**成立**。

### D. 导入环境（关键发现）

| 解释器 | sqlalchemy |
|--------|------------|
| `E:\项目\takton-alpha\.venv\Scripts\python.exe` | **OK**，核心模块全 import 通 |
| `PATH` 上的 `python` → `...\hermes-agent\venv\Scripts\python.exe` | **无 sqlalchemy** |

编制员工跑 `python -c` 时若走 PATH，就会复现「13 模块 sqlalchemy 失败」。  
**不是仓库缺依赖，是 command 工具未绑定项目 venv。**

正确类名抽样（venv）：

| 模块 | 真实导出 |
|------|----------|
| dispatcher | `WorkforceDispatcher` |
| inbox | `InboxService` |
| identity | `IdentityRegistry` |
| loop | `NexusAgentLoop` |
| memory_bus | 函数 `remember/recall` + `MemoryHit` 等（无 `MemoryBus` 类） |
| workflow_engine | `WorkflowEngine` |

`backend/agent/workflow_dispatch.py`：**确认不存在**（幽灵路径）；有 `workforce_dispatch.py`。

---

## 二、问题总表

### A. Takton 自己发现的（自审）

| ID | 问题 | 核实 | 真因归类 |
|----|------|------|----------|
| T1 | Services/Kernel 结构健康、TODO≈0 | ✅ 成立 | 正向结论 |
| T2 | `workflow_dispatch.py` 幽灵路径 | ✅ 成立 | 工单/文档路径过时 |
| T3 | 9 处类名 import 失败 | ✅ 成立 | **工单猜类名**，非代码缺失 |
| T4 | sqlalchemy 缺失致 13 模块失败 | ⚠️ 半真 | **PATH python=hermes venv**，非项目 .venv |
| T5 | qa Budget Exceeded，无产出 | ✅ 成立 | 任务过大 + 预算顶 |
| T6 | intent 雏形 / experience_sink 吞异常 / llm_scheduler 偏大 | ✅ 合理关注点 | 非阻塞债 |
| T7 | 建议 pip install sqlalchemy | ❌ 误导 | 应绑项目 venv，不是乱装包 |

### B. 我们（运维/工程）发现的

| ID | 问题 | 状态 |
|----|------|------|
| O1 | 同身份双 Run / 双进程 | **已修**（claim DB claimed + retire 残留进程） |
| O2 | 多 worker 内存 busy 不共享 | **已修**（Redis SETNX busy + 心跳） |
| O3 | 日志每条双写 | **已修**（root 仅 QueueHandler） |
| O4 | Run `iterations/token_used` 假零 | **已修**（recorder 回写） |
| O5 | 长任务预算顶死无法中途加 | **已修**（CEO top-up API） |
| O6 | workforce `python` 不走项目 venv | **已修（P0.1）** `project_python.rewrite_command_python` |
| O7 | 审计工单过大、类名瞎猜 | **已修（流程）** Playbook + audit_imports |
| O8 | 日志停更 vs 进程仍 running 的可观测性 | **已修（P2.3）** `stalled` + Kernel UI |

---

## 三、修复计划（按优先级）

### P0 — 本周必做（环境与编制执行正确性）✅ 已落地

| # | 项 | 做法 | 验收 | 状态 |
|---|-----|------|------|------|
| **P0.1** | **command/python 强制项目解释器** | `backend/core/project_python.py` + `executors.command` 重写 | 编制 `python -c` 走项目 venv | ✅ |
| **P0.2** | **导入审计标准脚本** | `scripts/audit_imports.py` | 项目 venv 下 26 项全绿 | ✅ |
| **P0.3** | **大审计拆单模板** | `docs/internal/CREW_AUDIT_PLAYBOOK.md` | 每单 ≤1 Part、禁猜类名 | ✅ |

### P1 — 短期 ✅ 已落地

| # | 项 | 做法 | 验收 | 状态 |
|---|-----|------|------|------|
| **P1.1** | 文档清幽灵路径 | 业务/手册仅保留审计报告中的「已核实幽灵」说明；正确路径 `workforce_dispatch` | 运行时代码无引用 | ✅ |
| **P1.2** | Kernel UI「加预算」 | `kernel/page.tsx` `+预算` → `topUpProcessBudget(+200k)` | 长任务可点 | ✅ |
| **P1.3** | experience_sink 日志级别 | `debug` → `warning` | 失败可观测 | ✅ |
| **P1.4** | 编制审计技能包 | Playbook §4 checklist + audit_imports | 可重复 | ✅ |

### P2 — 中期 ✅ 已落地

| # | 项 | 做法 | 状态 |
|---|-----|------|------|
| **P2.1** | llm_scheduler 拆分 | `llm_priority` / `llm_quota` / `llm_admission` + facade | ✅ |
| **P2.2** | intent 集成测试 | 声明 → 合成 → mediate（`test_intent_declare_synthesize_mediate_integration`） | ✅ |
| **P2.3** | 卡死可观测 | `last_charge_at` + `list_processes.stalled` + UI 标「疑似卡死」 | ✅ |
| **P2.4** | 双写/双 Run 回归 | `test_workforce_dual_run_and_budget` + Redis busy SETNX 单测 | ✅ |

### 明确不做 / 降级

| 项 | 原因 |
|----|------|
| 「全库 pip install sqlalchemy」当 P0 | 项目 venv 已有；乱装污染系统 |
| 为通过猜类名而改导出别名 | 应用正确 import，不迁就错误工单 |
| 恢复根 `tests/` 双轨 | 已统一 backend/tests |

---

## 四、建议实施顺序（可执行）

```
1) P0.1  workforce/command 绑定 sys.executable / TAKTON_PYTHON
2) P0.2  scripts/audit_imports.py + 可选 CI job
3) P0.3  审计派单模板（steward prompt / 文档）
4) P1.1  清幽灵路径
5) P1.2  Kernel 加预算按钮
6) P1.3  experience_sink warning
7) P2.*  按 dogfood 排期
```

**已完成、无需再开单**：O1–O5（双 Run、Redis busy、日志双写、Run 指标、CEO top-up API）。

---

## 五、一句话给老板

> 代码库结构健康（路由齐、测试可收集、Services/Kernel 几乎无 TODO）；自审最大误判是 **用错了 Python（hermes venv）当成缺 sqlalchemy**。  
> 真正要修的是：**编制执行环境绑死项目 venv**、**审计工单别猜类名/别塞太大**；双 Run/预算/日志双写已在工程侧修完。

---

## 六、附录：正确导入对照（审计用）

```text
from backend.kernel.dispatcher import WorkforceDispatcher
from backend.kernel.inbox import InboxService
from backend.kernel.identity import IdentityRegistry
from backend.kernel.kernel import AgentKernel
from backend.agent.loop import NexusAgentLoop
from backend.services.workflow_engine import WorkflowEngine
from backend.services import memory_bus  # remember / recall / supersede
from backend.services.cron_scheduler import CronScheduler
```
