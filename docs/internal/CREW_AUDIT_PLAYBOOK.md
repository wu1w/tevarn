# 编制审计派单手册（P0.3 + P1.4）

> 禁止「3 个 Part 塞一单」；禁止猜类名 import。

---

## 1. 拆单模板（每单一职责）

| 单号 | 角色建议 | instruction 要点 | 预算建议 |
|------|----------|------------------|----------|
| A | backend-engineer | 仅：用**项目 venv** 跑 `scripts/audit_imports.py`，汇报 ok/fail | 8 万 |
| B | agent-engineer | 仅：Services 指定目录 grep `^class` + TODO，出 markdown 表 | 15 万 |
| C | kernel-engineer | 仅：Kernel 指定模块清单 grep class/TODO | 15 万 |
| D | qa-engineer | 仅：对照 `routes/__init__.py` 与 `routes/*.py` 注册完整性 | 12 万 |
| E | qa-engineer | 仅：统计 `backend/tests` 数量 + `pytest --co -q` 是否可收集 | 12 万 |

**硬规则**

1. 一单只做上表一行。  
2. 命令必须用：  
   `E:\项目\takton-alpha\.venv\Scripts\python.exe`（或 `sys.executable` 已由 command 重写）。  
3. **禁止** `from X import GuessedClassName`；模块 import 成功即通过。  
4. 路径以仓库为准；不要编造 `workflow_dispatch.py` 等幽灵文件。

---

## 2. 正确 import 对照

```python
from backend.kernel.dispatcher import WorkforceDispatcher
from backend.kernel.inbox import InboxService
from backend.kernel.identity import IdentityRegistry
from backend.kernel.kernel import AgentKernel
from backend.agent.loop import NexusAgentLoop
from backend.services.workflow_engine import WorkflowEngine
from backend.services import memory_bus  # remember / recall
from backend.services.cron_scheduler import CronScheduler
```

---

## 3. CEO 中途加预算

```http
POST /api/kernel/processes/{id}/budget/top-up
{ "amount": 200000, "reason": "完成审计报告" }

POST /api/kernel/identities/{id}/budget/top-up-running
{ "amount": 200000, "also_default": true }
```

Kernel 页进程行也可点「+预算」。

---

## 4. 验收

- [x] `scripts/audit_imports.py` 在项目 venv 下 exit 0（26/26）
- [x] 路由注册完整（见 `reports/AUDIT_VERIFICATION_AND_FIX_PLAN_2026-07-30.md`）
- [x] 双 Run / Redis busy / 日志双写已有工程修复 + 单测
- [x] 报告写入 `reports/` 且注明 python 解释器路径
- [x] command 中 `python` 由 `project_python.rewrite_command_python` 绑项目 venv 
