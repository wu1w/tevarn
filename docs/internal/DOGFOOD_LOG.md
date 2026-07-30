# Dogfood 日志

> Phase 1 起每周至少一次真实开发任务 + 一次 PPT 任务。  
> 格式：日期 / 任务 / 卡点 / 严重度(高|中|低) / 备注  
> 该日志是各 Phase 排期微调的**唯一输入源**（见 `docs/DEV_PLAN_PHASE1-5.md`）。

---

## 模板

```
### YYYY-MM-DD
- 任务：
- 卡点：
- 严重度：
- 备注：
```

---

## 条目

（按周追加，新条目在上）

### 2026-07-30 · 马拉松真下发（3 路并行 · 目标 ≥2h）
- 任务：对 live BE 并行 enqueue 三类大型 inbox（见 `reports/DOGFOOD_MARATHON_DISPATCH.json`）
  1. **A 隔夜 Durable**：backend-engineer · 8 阶段全库审计落盘 `reports/dogfood_marathon/overnight_audit/` · item `af3e0346-…`
  2. **B PPT 交付**：agent-engineer · 28+ 页风格偏好多轮修订 `…/ppt_delivery/` · item `651a5901-…`
  3. **C Evolution QA**：qa-engineer · 回放上岗+真用+回归 `…/evolution_qa/` · item `34424523-…`
  - 预步骤：draft `good_apply_9a7f42` replay pass → **apply → active**
  - BE 以 `TAKTON_AGENT_INBOX_ITEM_TIMEOUT=10800`（3h）重启；身份 default_budget≈900k；进程 mid-run top-up +50 万
- 卡点：初启进程 budget 显示 50 万（payload/档案取整）；已 top-up。**须观察 ≥2h 是否超时/爆预算**；产出目录是否持续落盘
- 严重度：中（运行中）
- 备注：派发脚本 `scripts/dispatch_dogfood_marathon.py`；前端可看 `/tasks` `/kernel` `/agents` 收件箱

### 2026-07-30 · 隔夜 Run（live smoke）
- 任务：对运行中 Takton（FE `:3000` + BE `:8090`）冒烟 Durable/恢复路径——`GET /runs`（chat+inbox）、会话 checkpoint、`POST /sessions/{id}/resume`、`recover_stale_runs(auto_resume=False)`
- 卡点：当前会话 `can_resume=false`（无中断 checkpoint 可续）；**非**真 kill 隔夜，而是 API + recovery 干跑。重启 BE 后 `marked_interrupted=5` 证明非终态清扫活着
- 严重度：低
- 备注：`scripts/dogfood_live_smoke.py` 20/20；报告 `reports/DOGFOOD_LIVE_SMOKE.json`。真隔夜仍建议睡前派 inbox 再 kill 验证 auto-resume

### 2026-07-30 · PPT 偏好（live smoke）
- 任务：经 Kernel API 写入 identity `preference`（深蓝封面/少字多图/页脚版本号），读回命中；`memory_bus.remember/recall` 同步写图偏好；`GET .../growth` 200
- 卡点：旧 BE 进程曾缺 growth 路由（404）——需用当前代码重启 uvicorn；**未**跑真 LLM 30 轮出稿
- 严重度：低
- 备注：偏好已落库（identity + bus），下会话可依赖 recall；完整 PPT 成稿仍待真模型 dogfood

### 2026-07-30 · Evolution 回放上岗门禁（live smoke）
- 任务：FE `/evolution` 可开；`GET /evolution/status|assets`；对 draft `good_apply_*` 执行 `POST /evolution/drafts/{id}/replay` → **pass=true**（heuristic）
- 卡点：旧 BE 返回 405（无 replay 路由）；重启后 200。本轮只验证回放门禁，未点 apply 上岗真任务
- 严重度：低
- 备注：回放门禁可演示；「自产技能真实用过」仍待后续 apply + 任务引用

### 2026-07-30
- 任务：Phase 4 工程关账（回放门禁 / 成长档案 / 错误建议 / PPT 风格记忆 / bench 任务）
- 卡点：真实 LLM 下「公司 PPT 30 轮内成稿」需本机 dogfood 补跑；bench dogfood 任务为骨架
- 严重度：中
- 备注：当前无「严重度=高」未关闭条目；工程机制已合入 `feature/agent-kernel`；同日已补 live smoke 三条（上）

---
