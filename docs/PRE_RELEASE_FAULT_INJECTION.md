# 发布前故障注入清单（20 条）

**产品版本**：`0.5.0-alpha`  
**目的**：用可重复脚本 + 主路径 UI 证明「默认可依赖」，而非只证明架构正确。  
**硬门槛（发布前必须留证）**：

| # | 门槛 | 命令 / 证据 |
|---|------|-------------|
| A | Host 杀注入 30 轮 | `python scripts/host_marathon_gate.py --cycles 30 --inject-kill` → exit 0 |
| B | 2h soak（可夜间） | `python scripts/host_marathon_gate.py --hours 2` → exit 0，无人工干预 |
| C | 主路径恢复可点 | Chat：`RuntimeHealthBanner` 重启 Host + `ChatRecoveryCard` 续跑 |

产物建议落盘：`artifacts/release-gates/YYYYMMDD-host-marathon.txt`（stdout 全文）。

---

## 证据链约定

每条写：`PASS | FAIL | SKIP` + 时间 + 操作者 + 日志路径。  
`SKIP` 仅允许：无 WSL/bwrap 的沙箱 full 级项（须记 degraded 文案已展示）。

---

## 1–5 · Host 长稳与恢复

| # | 注入 | 步骤 | 期望 |
|---|------|------|------|
| 1 | ABI 门禁 | 启动 host；`list_methods` vs `REQUIRED_ABI_METHODS` | `host_marathon_gate` 首段 ABI OK；缺方法 fail-closed |
| 2 | 中途杀 host | gate 内 `--inject-kill`（约 cycle=cycles/3） | 自动 restart；后续 cycle resume 继续；`restart_count≥1` |
| 3 | 30 轮 suspend/resume | `--cycles 30 --inject-kill` | `resume rate ≥ 0.95`；`full_replay=false` |
| 4 | spill 跨恢复 | cycle 内 `result_spill` 后杀 host 再 `result_load`（可手扩 gate） | 句柄可取回或明确 not_found，不静默脏读 |
| 5 | 2h soak | `--hours 2` | 无人工；结束 rate 达标；audit 链不断 |

**脚本入口**：`scripts/host_marathon_gate.py`  
**相关**：`backend/kernel_rust/client.py`（watchdog / restart）、`process_recovery_plan`、`marathon_record`。

---

## 6–9 · Isolation / 沙箱诚实性

| # | 注入 | 步骤 | 期望 |
|---|------|------|------|
| 6 | OS 真 spawn | RPC `isolation_spawn_os` + `isolation_poll` | `os_pid` 有值；进程退出后 poll `running=false` |
| 7 | 超时 reap | 长命令 + `isolation_reap` 短 `max_age_secs` | 被 kill；status 非假 running |
| 8 | 沙箱能力探测 | `GET /api/kernel/runtime-health` 或 security check | `sandbox.level` ∈ full/restricted/none；none 时 issue+可操作 hint |
| 9 | degraded 文案 | Windows 无 WSL bwrap / Linux 无 bubblewrap | UI 不写「已进完整 OS 沙箱」；提示装 bwrap / WSL 或接受 job restricted |

**相关**：`crates/tevarn-kernel/src/isolation.rs`、`backend/computer/detect.py`、`backend/services/runtime_health.py`。

---

## 10–12 · 预算与 Soft renew

| # | 注入 | 步骤 | 期望 |
|---|------|------|------|
| 10 | 硬顶默认 | coding_research 默认路径：小预算跑到耗尽 | 默认 **不** 静默续到 2M；`soft_renew_max≤2` 或 soft 关 |
| 11 | 续额可见 | 允许 soft 时强制触发 1 次 renew | process meta `soft_renew_count`；Chat `RunCapabilityChip` 显示 soft×N；Kernel 页可见 |
| 12 | hard_cap_only | 设 `agent_budget_hard_cap_only=true` | 用尽即停；无 soft 事件 |

**相关**：`SoftRenewConfig`、`agent_budget_soft_renew_*`、`try_soft_renew_budget`。

---

## 13–15 · 生产逃生口 / Court

| # | 注入 | 步骤 | 期望 |
|---|------|------|------|
| 13 | DEV_UNSAFE 启动 | `TEVARN_DEV_UNSAFE=1` 起后端 | 启动大字警告日志；runtime-health `degraded_modes` 含 dev_unsafe（红项） |
| 14 | Court fail-closed | host 在线且 `agent_court_rust_required=true`，故意断 host 后再 tool | deny / 不静默 Python 放行 |
| 15 | Python backend 拒绝 | 生产 guard 下 `KERNEL_BACKEND=python` 无 DEV | 拒绝或仅显式降级 + 健康红项 |

**相关**：`production_guard.py`、`permission_court.py`、`runtime_health.degraded_modes`。

---

## 16–17 · Dispatcher / Scheduler

| # | 注入 | 步骤 | 期望 |
|---|------|------|------|
| 16 | 双 worker claim | 两进程同时 `inbox_claim` 同 identity 工单 | 仅一侧 SQL claimed；另一侧 release/空 |
| 17 | run_gate 真驱动 | 两 Agent 同 session / 跨 session 抢工具；压满 `run_gate` max | 排队/拒绝可观测；非仅 session 锁互斥 |

**相关**：`inbox_claim`、`dispatcher.tick`、`run_gate_*`、scheduler fair share。

---

## 18–20 · 主路径 UX 与状态一致

| # | 注入 | 步骤 | 期望 |
|---|------|------|------|
| 18 | 能力芯片 | coding_research 开一聊 | Chat 主路径见 Intent/能力/工具数（`RunCapabilityChip`） |
| 19 | 恢复卡片可点 | 人为 budget_exceeded / kill process 留可恢复 session | `ChatRecoveryCard` show；点续跑成功或明确失败 toast |
| 20 | host 重启 UI | 停 host 后打开 Chat | `RuntimeHealthBanner` error；「重启 Host」可点；恢复后 severity=ok |

**相关**：`frontend/app/chat/page.tsx`、`RuntimeHealthBanner`、`ChatRecoveryCard`。

---

## 建议执行顺序（打磨期）

```text
1 → 2 → 3 → 10 → 11 → 8 → 9 → 13 → 14 → 18 → 19 → 20 → 16 → 17 → 4 → 5 → 6 → 7 → 12 → 15
```

冻结新模块期间：**只扩 gate 证据与默认路径**，不新开 IPC/device_sync 功能面。

---

## 一键命令备忘

```bash
# A. 30 轮杀注入（发布硬门槛）
python scripts/host_marathon_gate.py --cycles 30 --inject-kill

# B. 2h soak
python scripts/host_marathon_gate.py --hours 2

# 沙箱探测（Python）
python -c "from backend.computer.detect import detect_sandbox_capability; print(detect_sandbox_capability())"

# 运行时健康（需后端 up）
curl -s localhost:PORT/api/kernel/runtime-health | jq .
```

---

## 变更记录

| 日期 | 说明 |
|------|------|
| 2026-07-31 | 初版：对照 marathon gate + recovery UX + isolation OS + soft renew 收紧 |
