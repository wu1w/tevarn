# Takton Alpha · AIOS 夜冲刺交付报告

**日期**：2026-07-28 ~ 2026-07-29  
**产品线**：alpha 私有线（不发布 GitHub）  
**版本锚点**：`0.4.6-alpha`（含 0.5 Durable / 0.6 预览增量）  
**仓库路径**：`E:\项目\takton-alpha`

---

## 1. 一句话结论

在主人睡眠期间，完成了 **运行记录默认收起** 与 **0.5→0.6 对齐切片**（通知、权限网、备份、全局 Runs、操作手册、测试全绿），产品仍处 **0.4.6-alpha + 预览**，**0.6.0 正式 DoD（连续 7 天真实使用）需主人在场收口**。

---

## 2. 已完成清单

### 2.1 体验 / IM 心智（既有 + 本夜）

| 项 | 状态 | 说明 |
|----|------|------|
| 运行记录默认收起 + 展开/收起 | ✅ | `SessionRunsPanel` 默认 `collapsed` |
| 企业 IM：一人一会话 | ✅（前期） | `POST /sessions/contact`；侧栏通讯录 |
| 项目组进度 | ✅（前期） | `project_groups` + `/chat?group=` |
| 死信台 UI | ✅ | 员工页 DeadLetterPanel |
| 审批中心文案 | ✅ | 「员工扩权 / 进化提案」 |
| 内核页：现在在跑 | ✅ | `GET /kernel/jobs/running` |
| 内核页：权限网 Tab | ✅ 本夜 | `policy.decision` 列表 |
| 内核页：一键备份 | ✅ 本夜 | 下载 JSON 快照 |
| 活动页：全局运行 | ✅ 本夜 | `GET /runs/recent` |

### 2.2 耐久 / 内核（0.5.0 核心）

| 项 | 状态 |
|----|------|
| Inbox dead + requeue/discard | ✅ |
| CRASH_RECOVERY 文档 | ✅ |
| reclaim / 超时熔断 | ✅（既有路径） |
| SQLite 权威存储决策 | ✅ STORAGE.md |

### 2.3 记忆 / 权限 / 0.6 体验（本夜重点）

| 项 | 状态 | 关键路径 |
|----|------|----------|
| MEMORY_AUTHORITY 写入权威 | ✅ | `docs/internal/MEMORY_AUTHORITY.md` |
| 记忆读取优先级测试 | ✅ | `backend/tests/kernel/test_memory_authority.py` |
| 工单完成/失败通知 | ✅ | `dispatcher._notify_owner`（字段 `data` 非 `meta`） |
| 待批扩权通知 | ✅ | `kernel._notify_escalation_pending` |
| policy.decision 事件 | ✅ | mediate / escalate 双写 |
| policy API | ✅ | `GET /kernel/policy/decisions` |
| 一键备份导出 | ✅ | `POST /kernel/backup/export` |
| 全局 Runs | ✅ | `GET /runs/recent` |
| AIOS_OPERATOR 手册 | ✅ | 一周自检清单 |
| 进化叙事合并 | ✅ | `EVOLUTION_NARRATIVE.md` |
| 模板员工 seed | ✅ | `backend/scripts/seed_template_crew.py` |
| 委托预算测试 | ✅ | parent→child 预留（既有逻辑 + 测） |

### 2.4 质量门

| 项 | 状态 |
|----|------|
| `backend/tests/kernel` 全量 | ✅ 绿（3 项 bwrap 环境 skip） |
| policy 事件导致断言修正 | ✅ stage23 / persistence_05 |
| 通知字段 bug 修复 | ✅ `meta` → `data` |
| `append_memory` 别名 | ✅ → `add_memory` |

---

## 3. 未完成 / TODO（主人醒来后）

### 3.1 必须主人在场（0.6.0 DoD）

- [ ] **手工**：杀后端 / kill -9 后工单可恢复（对照 `CRASH_RECOVERY.md`）
- [ ] **连续 7 天真实使用**无不可恢复数据丢失（`AIOS_OPERATOR.md` Day1–7 打勾）
- [ ] 主人无需看代码能处理：派活、停跑、批权、看记忆、看日报（体验验收）

### 3.2 建议后续切片（非阻断）

- [x] 审计只读独立路由 → `/audit`（2026-07-29 续开发）
- [x] 日报/周报一键已读 → `POST /kernel/workforce/report/read`（同上）
- [x] E4 统一停止 + F2 并发上限（同上；见 `AIOS_0.5_0.6_GAP_CONTINUATION_2026-07-29.md`）
- [x] 编制为空时 seed → API + 员工页 CTA（CLI 仍可用）
- [ ] 本地模型路径延迟基线记录
- [ ] TEE 技能 `auto_apply_skills` 默认与编制进化叙事进一步收敛（当前文档已区分）
- [ ] 公有发布 / 多租户（明确 **不做**，0.6 前 icebox）

### 3.3 明确不做

- 公有 GitHub 发版  
- 第五套记忆后端  
- 进化静默 `auto_apply` 改编制 caps  
- 整站重写为完整飞书  

---

## 4. 关键文件索引

### 后端

- `backend/kernel/dispatcher.py` — 工单通知  
- `backend/kernel/kernel.py` — policy.decision / 扩权通知  
- `backend/kernel/identity.py` — `append_memory` 别名  
- `backend/api/routes/kernel.py` — policy / backup / jobs/running / dead letter  
- `backend/api/routes/runs.py` — `/runs/recent`  
- `backend/repositories/agent_run_repo.py` — `list_recent`  
- `backend/scripts/seed_template_crew.py`  
- `backend/tests/kernel/test_policy_and_notify.py`  
- `backend/tests/kernel/test_memory_authority.py`  
- `backend/tests/kernel/test_inbox_dead_letter.py`  

### 前端

- `frontend/components/chat/SessionRunsPanel.tsx`  
- `frontend/app/kernel/page.tsx`  
- `frontend/app/activity/page.tsx`  
- `frontend/app/approvals/page.tsx`  
- `frontend/lib/api.ts`  

### 文档

- `docs/internal/AUTONOMOUS_SPRINT_0.5_to_0.6.md`  
- `docs/internal/ROADMAP_0.4.5_to_0.6.md`  
- `docs/internal/AIOS_OPERATOR.md`  
- `docs/internal/MEMORY_AUTHORITY.md`  
- `docs/internal/CRASH_RECOVERY.md`  
- `docs/internal/EVOLUTION_NARRATIVE.md`  
- `docs/internal/STORAGE.md`  
- `CHANGELOG.md`  

---

## 5. 本地运行（alpha）

```text
后端 :8090  TAKTON_AIOS_PROFILE=aios-dev  SQLite
前端 :3000  NEXT_PUBLIC_API_URL=/api
```

测试：

```bash
PYTHONPATH=. python -m pytest backend/tests/kernel -q
```

---

## 6. 版本与产品定位

| 标签 | 含义 |
|------|------|
| 0.4.6-alpha | 产品文件版本号（package / VERSION 等） |
| 0.5 Durable 预览 | 死信、jobs/running、崩溃文档、通知 |
| 0.6 预览 | 权限网、备份、全局 Runs、操作手册、模板员工 |
| 0.6.0-alpha | **未封版** — 依赖主人 7 天自用 DoD |

---

## 7. 压缩包说明

本报告与源码一并打包。打包 **排除**：

- `.venv/`、`node_modules/`、`frontend/.next/`  
- `*.db`、`.env`、密钥类文件  
- 大体积构建产物 / 日志 / uploads  

**包含**：backend、frontend 源码、docs、tests、scripts、本报告、CHANGELOG 等。

---

*报告生成：Grok 自动夜冲刺 · 2026-07-29*
