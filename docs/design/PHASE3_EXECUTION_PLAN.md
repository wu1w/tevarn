# Phase 3 执行详规（记忆总线 + 权限一张网 + 前端补缺）

> 开工：2026-07-30 · 目标版本：0.5.x  
> 原则：**统一表面，不新建第五套记忆/第二套权限心智**；前端只补三页。

---

## 0. 目标与关账

| Gap | 消灭方式 |
|-----|----------|
| G4 记忆多套 | `memory_bus` 唯一业务写入口 + 权威表路由 + RAG 仅索引 |
| G5 权限散落 | `permission_court` 单一决策器 + 可解释审计 |
| 能力不可见 | Evolution / Kernel / Tasks(Run 时间线) 三页产品化 |

**工程关账**：3.1–3.3 checkbox 全绿 + 相关单测绿 + P1–P3 全量回归绿。  
**体验关账（dogfood）**：PPT 风格偏好跨会话 recall；权限决策能在审计中看到 layer/rule。

---

## 1. 切片总览（依赖序）

```
3.1 Memory Bus  ──►  3.2 Permission Court  ──►  3.3 FE 三页
       │                      │                      │
       ▼                      ▼                      ▼
  写入口 + recall        mediate/hooks 收口      看见 Run/裁决/进化
```

| 切片 | 工期感 | 交付物 | 风险 |
|------|--------|--------|------|
| **3.1a** 设计冻结 | 0.5d | `MEMORY_BUS.md` | 表定死不返工 |
| **3.1b** 总线 facade | 1–2d | `memory_bus.py` remember/recall/supersede | 兼容旧 API |
| **3.1c** 写入归口 | 1–2d | tools/crew/entity 改走总线 | 测试回归 |
| **3.1d** entity 版本链 | 1d | alembic 0003 + supersede | 迁移 SQLite |
| **3.2a** Court | 1–2d | `permission_court.py` | 优先级与 workforce |
| **3.2b** 接线审计 | 1d | mediate + hooks + policy.decision 字段 | fail-closed |
| **3.3a** Tasks→Runs | 1d | `/tasks` 统一 Run 时间线 | API 已有 |
| **3.3b** Evolution diff | 0.5–1d | draft 内容预览 + enable | 双系统混淆 |
| **3.3c** Kernel suspend | 0.5d | HTTP + 按钮 | 无 await 红线 |
| **3.3d** locale 拆分 | 1d | zh/en JSON + 瘦 store | 键面巨大 |
| **P1–3 联调** | 0.5d | 全量 pytest + FE + 冒烟 | CI |

---

## 2. 3.1 记忆总线 — 详细设计

### 2.1 权威表（冻结）

| kind 族 | 权威存储 | 谁能写 | 冲突裁决 | 向量 |
|---------|----------|--------|----------|------|
| identity（persona/duty/methodology/preference/experience） | `identity_memory` | Identity + 审批 | supersede 版本链 | 随写同步 |
| fact/entity | `entities` | 任何 Run（经总线） | supersede / archive | 可选 |
| graph（knowledge/decision/preference/experience） | `memory_nodes` | agent 工具 / 总线 | supersede 节点 | 否（本期） |
| wiki 文档 | wiki 表 | 用户 + 审批 agent | 人工 | 索引层 |
| RAG | Qdrant | **禁止业务直写** | n/a | 仅索引 |

### 2.2 API 面

```text
remember(kind, content, *, source_run_id, confidence, identity_id?, user_id?, title?, tags?, meta?)
supersede(ref, new_content, *, approved_by, source_run_id?)
recall(query, kinds?, top_k, identity_id?, user_id?) -> list[{source, id, kind, content, freshness, score}]
```

### 2.3 迁移策略（非大爆炸）

1. 总线内部调用现有 `IdentityRegistry` / `AsyncMemoryGraphRepository` / entity service。  
2. **对外业务写点**改走总线：`memory_tools`、`CrewMemoryWriter.record_manual`、entity create（工具路径）。  
3. Registry 底层 API 保留，标为 bus-internal；验收用测试断言业务路径经 bus。  
4. Wiki 人类导入可保留直写；agent 侧 wiki 写走总线 `kind=wiki`（若本期时间紧可只文档约定）。

### 2.4 测试

- `test_memory_bus.py`：remember identity → recall 命中；supersede 后旧版不出现。  
- 现有 `test_crew_memory` / `test_memory_graph` / `test_memory_authority` 保持绿。

---

## 3. 3.2 权限一张网 — 详细设计

### 3.1 决策优先级（定死）

```
secret_floor deny
  > user deny
  > skill contract deny
  > path deny (ToolPermissionManager)
  > user allow
  > profile / PermissionGate
  > capability (mediate 进程能力集)
  > default ask (危险写工具) / allow (只读兼容)
```

Workforce：`steward` 层短路，**不**弹主人窗；仍写可解释记录。

### 3.2 Court 输出

```json
{
  "tool": "file_read",
  "args_digest": "sha256:…",
  "verdict": "allow|deny|ask",
  "matched_rule": "secret_floor:*.pem",
  "layer": "secret_floor",
  "reason": "…"
}
```

写入 `policy.decision` 哈希链（扩展现有 `_emit_policy_decision`）。

### 3.3 接线

| 入口 | 行为 |
|------|------|
| `tool_hooks.builtin_permission_before` | 调 `court.decide_tool` |
| `kernel.mediate` | 能力层经 court；policy 事件带 court 字段 |
| `tools/registry` 路径检查 | 可委托 court 或 court 内调 Manager |

### 3.4 测试

迁移/扩展：`test_permission_fail_closed`、`test_permission_rule_matching`、`test_skill_contract`、kernel mediate 测试。  
新增 `test_permission_court.py`：优先级 + 可解释字段。

---

## 4. 3.3 前端补缺 — 详细设计

| 页 | 现状 | 目标 |
|----|------|------|
| `/evolution` | 草稿 apply/reject | + content/diff 预览；enable/disable；链到审批中心 rollback |
| `/kernel` | 进程/事件/policy 已有 | + suspend/resume 进程；policy 展示 layer/rule |
| `/tasks` | 会话 Task 板 | **主视图 = 全局 Run 时间线**（origin/status/checkpoint/续跑）；Task 板降为次 tab |
| localeStore | ~4k 行内联 | `locales/zh.json` + `en.json` + 瘦 store |
| GAP 2 处 | 扫描假阳性 | 重跑报告注明 false-positive |

---

## 5. 明确不做（Phase 3 冻结）

- 不新建第七种执行心智 / 新记忆表族  
- 不补 GAP 中其余 57 个前端缺口  
- 不做 Phase 4 回放验证 / 身份成长档案大页  
- 不强制 Wiki 人类导入改总线（文档约定即可）

---

## 6. 验收清单（工程）

- [x] `MEMORY_BUS.md` + `memory_bus` + 测试绿（含 wiki 真写）  
- [x] `permission_court` + `test_permission_court` 优先级矩阵  
- [x] FE 三页可操作；locale 拆分后 lint/tsc/build 绿  
- [x] Intent 生产接线：`apply_intent_to_process` + loop/subagent  
- [x] `DEV_PLAN_PHASE1-5.md` Phase 3 checkbox 全勾 + 完成日期  

---

## 7. 提交节奏

1. `docs: Phase3 详规 + MEMORY_BUS`  
2. `feat(memory): memory_bus + entity supersede + tests`  
3. `feat(kernel): permission_court + mediate/hooks wire`  
4. `feat(fe): tasks runs timeline + evolution/kernel + locales`  
5. `chore: Phase3 关账 + DEV_PLAN`  

（可按切片 squash，但必须可回归。）
