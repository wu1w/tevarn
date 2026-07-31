# P0.5 进度（0.7 长程可靠与效率）

**开闸**：2026-07-31  
**状态**：**已完成** — 见 `docs/P05_COMPLETION_REPORT.md`  
**目标版本**：0.7.0

---

## 全部项

| ID | 内容 | 状态 |
|----|------|------|
| E1–E6 | 首批核心 | ✅ |
| R1 | Provider `cache_record` | ✅ `usage_normalize.log_cache_usage` |
| R2 | marathon soak + 阈值 | ✅ `scripts/marathon_soak.py` |
| R3 | persistence ← Rust snapshot | ✅ `project_rust_snapshots` |
| R4 | 预算/doom 文案 + resume API | ✅ `exit_reasons` · `/kernel/policy` |
| R5 | 成本面板 | ✅ `/kernel/cost` · `cost_panel` |

### 联调

```powershell
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
cargo test -p takton-kernel
cargo build -p takton-kernel-host
Copy-Item target\debug\takton-kernel-host.exe vendor\takton-kernel-host\ -Force

$env:TAKTON_KERNEL_BACKEND="rust"
$env:PYTHONPATH="."
python scripts/smoke_p05_marathon.py
python scripts/marathon_soak.py --cycles 40
python -m pytest backend/tests/kernel/test_p05_longrun.py -q
```
