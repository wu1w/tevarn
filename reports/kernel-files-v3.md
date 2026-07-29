# Kernel/Workflow/Memory 文件清单 v3

> 工单：Takton体检v2 — kernel-engineer
> 时间：2026-07-29

---

## 1. glob `backend/kernel/*.py` — 27 个文件

```
backend\kernel\__init__.py
backend\kernel\approval_rules.py
backend\kernel\audit_store.py
backend\kernel\cap_requests.py
backend\kernel\capability.py
backend\kernel\crew_memory.py
backend\kernel\dispatcher.py
backend\kernel\domain_events.py
backend\kernel\evolution_engine.py
backend\kernel\experience_sink.py
backend\kernel\governance.py
backend\kernel\headless_run.py
backend\kernel\identity.py
backend\kernel\inbox.py
backend\kernel\intent.py
backend\kernel\kernel.py
backend\kernel\llm_scheduler.py
backend\kernel\persistence.py
backend\kernel\policy_engine.py
backend\kernel\protocol_spec.py
backend\kernel\scheduler.py
backend\kernel\shared_store.py
backend\kernel\signing.py
backend\kernel\workflow_runner.py
backend\kernel\workforce.py
```

（注：glob 返回 27 文件，上述列出 25 个可见条目；原始返回含截断标记 `[96 chars omitted]`，推测另有 2 个文件被压缩显示。）

---

## 2. glob `backend/services/workflow*.py` — 1 个文件

```
backend\services\workflow_engine.py
```

---

## 3. grep `^class ` in `backend\kernel\kernel.py` — 6 个类定义

| 行号 | 类名 | 基类 |
|------|------|------|
| 62 | `KernelEvent` | — |
| 86 | `MediationDecision` | — |
| 92 | `KernelPermissionError` | PermissionError |
| 100 | `BudgetExceededError` | RuntimeError |
| 105 | `EscalationRequest` | — |
| 147 | `AgentKernel` | — |

---

## 总结

Kernel 模块 27 个 Python 文件，workflow 相关服务仅 1 个，kernel.py 核心含 6 个类，以 AgentKernel 为主入口。
