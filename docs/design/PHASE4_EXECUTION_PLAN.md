# Phase 4 执行详规（可信自进化 + 主循环手感）

> 开工：2026-07-30 · 目标版本：0.6.0  
> 原则：**回放验证是差异化护城河**；档案与手感用现有表/API 聚合，不大爆炸重写。

---

## 0. 目标与关账

| 目标 | 方式 |
|------|------|
| G7 进化浅 | `replay_validator` 门禁：回放失败不得 apply/approved |
| 身份成长可见 | `GET .../growth` + `/agents/[id]` |
| 主循环手感 | 错误建议、chat 续跑、PPT 风格记忆、bench 固定集 |

**工程关账**：4.1–4.3 checkbox 绿 + 全量测试 + CI。  
**体验关账**：PPT≤30 轮 / dogfood 由本人补；工程侧提供机制与 bench 任务。

---

## 1. 切片

| 切片 | 交付 | 风险 |
|------|------|------|
| **4.1a** replay_validator | 指标对比 + meta 写入 | 无 LLM 时 mock 路径 |
| **4.1b** apply 门禁 | 失败回放阻断 apply | 兼容旧 draft 无 meta |
| **4.1c** scoreboard 复查 | settings 文档 + 可选 API | 阈值不改默认 |
| **4.2** growth API + 页 | 聚合 + 时间线/曲线 | 身份 id 路由 |
| **4.3a** 错误建议 | sanitize + next step | 不泄密 |
| **4.3b** chat 续跑 CTA | stop 后 resume | 复用 API |
| **4.3c** PPT | memory_bus 风格 + workspace 中间件 | 无 python-pptx 时 MD |
| **4.3d** bench | ppt + dogfood yaml | 不强制真 LLM CI |

---

## 2. 4.1 回放验证设计

```text
draft (status=draft)
  → validate_skill_replay(asset)
      解析 content / origin trajectory
      静态结构检查 + 模拟「挂载技能后重跑」指标
      （MVP：用轨迹工具错误率/步数/完成标记 对比；无轨迹则跑结构启发式）
  → meta.replay = { pass, metrics, baseline, delta, reason }
  → apply_draft: if replay.pass is False → 400
```

配置：
- `agent_evolution_require_replay`（默认 true）
- `agent_evolution_replay_max_tool_error_rate`（默认 0.4）
- scoreboard 沿用既有 `agent_evolution_score_*`

---

## 3. 4.2 成长档案

`GET /kernel/identities/{id}/growth` →
```json
{
  "identity": {...},
  "memory_timeline": [{id, kind, version, content, superseded_by, created_at}],
  "skills": [{name, gen, success_rate, samples, outcomes[]}],
  "runs": {total, done, failed, avg_iterations, token_used}
}
```

FE：`/agents/[id]` 全页；抽屉链到全页。

---

## 4. 4.3 手感

- 错误：`_sanitize_tool_error` + 按异常类型/工具名给「下一步建议」
- Chat：streaming 结束后若 interrupted/stopped → 显示「一键续跑」
- PPT：recall preference 注入 prompt；outline JSON 写 workspace；支持 resume_from
- bench：`ppt_01_company_style.yaml` + 3 个 dogfood 开发任务骨架

---

## 5. 不做

- 不重写 distiller/scoreboard 核心  
- 不新建第五套记忆  
- 不补 GAP 其余前端缺口  
