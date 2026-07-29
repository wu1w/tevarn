# AIOS 0.5 / 0.6 缺口续开发报告

**日期**：2026-07-29  
**产品线**：alpha 私有（`0.4.6-alpha` + 0.5/0.6 预览）  
**工作树**：`takton-alpha-aios-0.6-sprint-20260729-0711`

---

## 1. 结论

夜冲刺已覆盖死信、权限网、备份、Runs、记忆权威、通知等。本切片补齐 **代码可交付** 的剩余项：

| 项 | 状态 |
|----|------|
| E4 统一停止（process + agent + inbox） | ✅ 本切片 |
| F2 全局/身份并发上限 | ✅ 本切片（身份默认串行 1，全局默认 8） |
| 日报一键已读 | ✅ 本切片 |
| 审计只读页 H3 | ✅ 本切片 `/audit` |
| 空编制 seed | ✅ 本切片 API + 员工页 CTA |
| 主人在场 DoD（kill-9 / 7 天 / 无码 UX） | ⏸ 非纯代码 |
| 公有发布 / 多租户 / auto_apply caps | ❌ icebox |

---

## 2. 本切片实现索引

### 后端

- `backend/kernel/inbox.py` — `cancel()` → status=`cancelled`
- `backend/kernel/dispatcher.py` — `_item_tasks` / `cancel_job` / 全局并发 cap
- `backend/core/config.py` — `agent_dispatcher_max_global_concurrent` / `_identity_concurrent`
- `backend/api/routes/kernel.py` — `POST /jobs/stop`、`POST /workforce/report/read`、`POST /workforce/seed-template-crew`；report 含 `has_unread`
- `backend/scripts/seed_template_crew.py` — 可复用 `seed_template_crew(registry)`
- `backend/tests/kernel/test_stop_concurrency_report.py`

### 前端

- `frontend/app/kernel/page.tsx` — Live jobs **停止**按钮
- `frontend/app/agents/page.tsx` — 日报 **标记已读**；空态 **一键预置模板员工**
- `frontend/app/audit/page.tsx` — 审计只读（系统日志 / 内核事件 / 权限网）
- `frontend/lib/api.ts` — `stopRunningJob` / `markWorkforceReportRead` / `seedTemplateCrew` / `listAuditLogs`
- `frontend/components/layout/AgentSidebar.tsx` — 导航「审计」

---

## 3. 仍需主人在场

- 杀后端 / kill -9 后对照 `docs/internal/CRASH_RECOVERY.md`
- 连续 7 天真实使用（`AIOS_OPERATOR.md`）
- 无代码路径体验验收：派活、停跑、批权、看记忆、看日报

---

## 4. 配置

| 环境变量风格字段 | 默认 | 含义 |
|------------------|------|------|
| `agent_dispatcher_max_global_concurrent` | 8 | 全局同时在跑工单数（0=不限制） |
| `agent_dispatcher_max_identity_concurrent` | 1 | 单身份并发（实现上 busy 集合串行） |

---

## 5. 测试

```bash
cd <worktree>
PYTHONPATH=. python -m pytest backend/tests/kernel/test_stop_concurrency_report.py -q
PYTHONPATH=. python -m pytest backend/tests/kernel -q
```
