# 债 2–4 完成报告（观测周报 · 包市场 · WASM 加深）

**日期**：2026-07-31  
**状态**：✅ 已落地  
**前置**：债 #1 硬化见 `HARDENING_REPORT.md`

---

## 1. 对照

| # | 债 | 状态 | 要点 |
|---|----|------|------|
| 2 | 观测 / Eval 周报打穿 | ✅ | cost·cache·marathon·eval·pkg·wasm 合成周快照 + 趋势 |
| 3 | 包市场 / 签名扫描产品化 | ✅ | catalog·scan·promote·安装镜像 Kernel |
| 4 | WASM skill 运行时加深 | ✅ | memory/stack/max_ops/cap 门控/WAT 导入导出/线性内存 |

---

## 2. 债 #2 — 观测 / Eval 周报

### 新增
- `backend/services/weekly_report.py` — 采集、健康分、落盘、趋势
- `scripts/takton_weekly_report.py` — CLI（`--run-eval`）
- `scripts/takton_eval.py` — 结果持久化到 `data/eval/runs/`
- API：
  - `GET /api/kernel/weekly`
  - `POST /api/kernel/eval/run`

### 产物路径
```
data/eval/runs/<ts>.json
data/eval/runs/latest.json
data/eval/weekly/<YYYY-Www>.json
data/eval/weekly/latest.json
```

### 健康分 parts
`eval` · `cache_hit_rate` · `marathon_resume` · `pkg_clean` · `wasm_engine`

---

## 3. 债 #3 — 包市场产品化

### Rust `package_mgr`
- `scan_only` / `pkg_scan`
- `promote` / `pkg_promote`（仅 clean+签名 → verified；force 只标 reviewed）
- `catalog` / `pkg_catalog`
- status 增加 verified/market/signing 字段

### Python
- `backend/packages/market.py` — zip 安装 + Kernel 镜像
- API：
  - `GET /packages/market`
  - `POST /packages/market/scan|install|promote|activate`
  - `POST /packages/install` 默认 `mirror_kernel=true`
  - `GET /api/kernel/packages/catalog`

### 流程
```
zip → 解压到 install_root → pkg_sign + pkg_install
  → verified | quarantined
  → promote（重扫）→ verified → activate
```

---

## 4. 债 #4 — WASM 加深 → 真 wasmtime（见 `WASMTIME_REPORT.md`）

### Engine `wasmtime_cranelift` + fallback `hostcall_ledger`
| 能力 | 说明 |
|------|------|
| **wasmtime 28 + Cranelift** | 真机器码执行 WAT / `\0asm` |
| fuel | Store fuel 计量 |
| memory | StoreLimits 页上限 |
| host imports | env.log/clock/abort/cap_check |
| hostcall_ledger | 非法模块 / ops harness 回退 |
| unload / kill | `wasm_unload` / `wasm_kill` |

**诚实边界**：非 Component Model；tight loop + fuel 在 Windows 上勿做破坏性测试。

---

## 5. 验收

```powershell
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
# 停旧 host 后重建
Get-Process takton-kernel-host -ErrorAction SilentlyContinue | Stop-Process -Force
cargo test -p takton-kernel
cargo build -p takton-kernel-host
$env:TAKTON_KERNEL_HOST_BIN = (Resolve-Path target\debug\takton-kernel-host.exe)
$env:TAKTON_KERNEL_BACKEND = "rust"
$env:PYTHONPATH = "."

python -m pytest backend/tests/kernel/test_debts_2_4.py backend/tests/kernel/test_hardening_tool_gate.py -q
python scripts/takton_eval.py
python scripts/takton_weekly_report.py
python scripts/smoke_p2_integration.py
```

---

## 6. 文件清单

**新增**
- `backend/services/weekly_report.py`
- `backend/packages/market.py`
- `scripts/takton_weekly_report.py`
- `backend/tests/kernel/test_debts_2_4.py`
- `docs/DEBTS_2_4_REPORT.md`

**修改**
- `crates/takton-kernel/src/wasm_runtime.rs`
- `crates/takton-kernel/src/package_mgr.rs`
- `crates/takton-kernel/src/kernel.rs`
- `crates/takton-kernel/src/lib.rs`
- `crates/takton-kernel-host/src/main.rs`
- `scripts/takton_eval.py`
- `backend/api/routes/kernel.py`
- `backend/api/routes/packages.py`
