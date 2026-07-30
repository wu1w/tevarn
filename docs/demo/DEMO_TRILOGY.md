# 首发 Demo 三连（Phase 5.2c）

> 版本：`0.4.10-alpha`  
> 目标：用已有 P2/P3/P4 机制，**可跟做**演示（录屏可选）。

---

## Demo 1 — kill 后任务爬起来（Phase 2 Durable Run）

**故事**：长任务被 kill，重启后 inbox/cron 自动续跑或标记 interrupted。

### 步骤

1. 启动：`python start.py`（或仅 backend `:8090`）
2. 编制派一条长 inbox 工单（审计/写报告），确认 `GET /api/runs?origin=inbox` 有 `running`/`executing`
3. **强制结束** backend 进程（任务管理器 / `Stop-Process`）
4. 再启动 backend（`agent_run_auto_recover=true` 默认）
5. 观察：
   - 非终态 Run → `interrupted`
   - inbox/cron/headless 按策略 auto-resume
   - chat 仅标记，需用户「一键续跑」

### 验收

- [ ] Run 列表可见 status 变化  
- [ ] 工单最终有结果或明确 interrupted + 可续  

### 相关代码

- `backend/agent/run_recovery.py`
- `docs/design/EXECUTION_MODEL.md` §Durable

---

## Demo 2 — 技能回放验证上岗（Phase 4 Evolution）

**故事**：草稿技能经 replay 门禁，通过后 apply，失败不得上岗。

### 步骤

1. 打开 `/evolution`
2. 选一条 draft（或触发 evolution 分析生成）
3. 点 **Replay** → 见 `meta.replay.pass` / 失败原因
4. `agent_evolution_require_replay=true` 时：fail 不得 apply
5. pass 后 apply → 审批中心如需再批 → 真实任务用该技能

### 验收

- [ ] fail 时 apply 被拒  
- [ ] pass 后可上岗  

### 相关代码

- `backend/evolution/replay_validator.py`
- `POST /api/evolution/drafts/{id}/replay`

---

## Demo 3 — 权限审计可解释（Phase 3 Court）

**故事**：每次工具调用可在 Kernel 看到 layer / rule / verdict。

### 步骤

1. 开一个显式能力受限的进程/员工（如仅 `file_read`）
2. 让 agent 读文件（应 allow）再尝试写/终端（应 deny）
3. 打开 `/kernel` → **policy / mediate** 事件
4. 确认字段：`layer`、`matched_rule`、`verdict`、`args_digest`

### 验收

- [ ] allow/deny 成对可见  
- [ ] 能向观众解释「为什么被拒」  

### 相关代码

- `backend/kernel/permission_court.py`
- `docs/design/MEMORY_BUS.md`（记忆）· court 决策链

---

## 录屏建议（可选）

| 段 | 时长 | 画外音 |
|----|------|--------|
| 1 | 2–3 min | kill / 重启 / Run 状态 |
| 2 | 2 min | replay fail vs pass |
| 3 | 2 min | Kernel 审计行 |

托管：GitHub Release 附件或 docs 链。无视频时本页即可作发版说明。
