# P0.5 完成报告（0.7 长程可靠与效率）

**日期**：2026-07-31  
**状态**：**P0.5 工程完成**（路径与指标齐备；真 2h 墙钟 soak 可选夜间跑）  
**配套**：`docs/P05_PROGRESS.md` · `docs/IMPLEMENTATION_PLAN_P0_P2.md` · `docs/kernel-abi-v1.md`

---

## 1. 范围对照

| ID | 工作项 | 状态 |
|----|--------|------|
| E1 | 进程快照 + tail_hash 恢复 | ✅ |
| E2 | 工具大结果外置句柄 | ✅ |
| E3 | iteration / doom 内核策略 | ✅ |
| E4 | 进程树 cap/资源回收 | ✅ |
| E5 | cache 指标 + Provider 上报 | ✅ |
| E6 | 马拉松路径 + soak 脚本 | ✅ |
| R1 | Provider `cache_record` 实路径 | ✅ `log_cache_usage` |
| R2 | marathon soak + `marathon_resume_success` | ✅ |
| R3 | persistence 消费/投影 Rust snapshot | ✅ `project_rust_snapshots` |
| R4 | 预算/doom 文案与恢复入口 | ✅ `exit_reasons` + API |
| R5 | 三维成本面板 API | ✅ `/kernel/cost` |

---

## 2. 关键 API

| 方法 / 路由 | 说明 |
|-------------|------|
| `process_snapshot` / `process_recovery_plan` | 快照与禁止 full_replay 的恢复计划 |
| `result_spill` / `result_load` | 大结果外置 |
| `iteration_*` / `doom_*` | 长程策略 |
| `cache_record` / `cache_metrics` | family 级命中率 |
| `cost_charge` / `cost_panel` / `cost_process` | token / billable 账本 |
| `marathon_record` / `marathon_metrics` | 恢复成功率 |
| `GET /api/kernel/cost` | 三维成本面板 |
| `GET /api/kernel/policy/{id}` | 策略 + resume 入口 |
| `GET /api/kernel/recovery/{id}` | 恢复计划 |
| `GET /api/kernel/cache/metrics` | 缓存仪表 |
| `GET /api/kernel/marathon/metrics` | 马拉松指标 |
| `GET /api/kernel/exit_reasons/{code}` | 退出码文案 |
| `POST /api/kernel/processes/{id}/resume` | 已有恢复入口 |

---

## 3. 复现命令

```powershell
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
cargo test -p takton-kernel
cargo build -p takton-kernel-host
Copy-Item target\debug\takton-kernel-host.exe vendor\takton-kernel-host\ -Force

$env:TAKTON_KERNEL_BACKEND="rust"
$env:PYTHONPATH="."
python scripts/smoke_p05_marathon.py
python scripts/marathon_soak.py --cycles 40 --threshold 0.95
python -m pytest backend/tests/kernel/test_p05_longrun.py -q

# 可选：2 小时墙钟 soak
# $env:MARATHON_SOAK_SECONDS="7200"
# $env:MARATHON_CYCLES="120"
# python scripts/marathon_soak.py
```

---

## 4. 出口标准核对

| 标准 | 结论 |
|------|------|
| 马拉松恢复成功率可测且默认阈值 ≥0.95 | ✅ soak 脚本硬阈值 |
| `cache_hit_rate` 按 family 可查 | ✅ cache_metrics + API |
| 中断可解释（suspend / budget / doom） | ✅ exit_reasons + policy API |
| 恢复路径禁止 full_replay | ✅ recovery_plan + persistence |
| 成本三维可聚合 | ✅ cost_panel + live resources |
| 回归绿 | 见联调 |

---

## 5. 诚实边界

- **真 2h 墙钟 soak** 默认不在 CI 跑；用 `MARATHON_SOAK_SECONDS=7200` 夜间执行。  
- Provider 缓存上报依赖 usage 回填；无 usage 的流式路径只记 cost 粗估。  
- OS 级硬沙箱执行体仍在 Python computer；Rust 管策略/账本/快照。  
- Electron 安装包仍不在 P0.5 范围。

---

## 6. 结论

**P0.5 / 0.7 长程可靠与效率底座已落地**：快照恢复、结果外置、策略熔断、树回收、缓存与成本可观测、马拉松指标与 soak 门禁齐备，可进入 **P1（IPC / 系统服务）**。
