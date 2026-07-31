# Wasmtime 真机执行落地报告

**日期**：2026-07-31  
**状态**：✅ 已接入（小心复核）

---

## 1. 做了什么

将 WASM skill 运行时主引擎从「hostcall 账本模拟」升级为 **wasmtime 28 + Cranelift**：

| 能力 | 实现 |
|------|------|
| 真编译执行 | `Module::new` 接受 **WAT / `\0asm`**，Cranelift 生成机器码 |
| Fuel 计量 | `Config::consume_fuel(true)` + `Store::set_fuel` |
| 内存上限 | `StoreLimitsBuilder::memory_size(pages * 64KiB)` |
| Host 导入 | `env.log` / `env.clock` / `env.abort` / `env.cap_check`（+ `takton.log`） |
| 回退 | 非法模块 / `params.ops` harness → **hostcall_ledger**（兼容旧 smoke） |

`wasm_status.engine` = `wasmtime_cranelift`，invoke 结果带 `engine: "wasmtime" | "hostcall_ledger"`。

---

## 2. 依赖

```toml
# workspace
wasmtime = { version = "28", features = ["cranelift", "wat", "parallel-compilation"] }
```

`takton-kernel` 增加：`wasmtime`、`anyhow`。

---

## 3. 调用约定

```text
wasm_load(name, content=WAT|binary, fuel_limit, memory_pages)
  → wasmtime_ready=true 时已编译

wasm_invoke(module_id, entry, params={})
  → 真 wasmtime 调 export（main / _start / run）

wasm_invoke(..., params={ "ops": [...], "engine": "hostcall" })
  → 强制走 hostcall 账本（测试/兼容）
```

---

## 4. 验收（轻量，避免死循环/杀进程）

```powershell
# 仅 lib 单测（已验证 7 passed）
cargo test -p takton-kernel --lib wasm_runtime::tests -- --test-threads=1

# host 构建后联调
cargo build -p takton-kernel-host
$env:TAKTON_KERNEL_HOST_BIN = (Resolve-Path target\debug\takton-kernel-host.exe)
$env:TAKTON_KERNEL_BACKEND = "rust"
$env:PYTHONPATH = "."
python scripts/smoke_p2_integration.py
```

**不做**：故意耗尽 fuel 的巨循环、无限 `br` 测试（Windows 上曾触发不可恢复 abort）。

---

## 5. 诚实边界

- 组件模型（Component Model）未开；仅 core WASM module。  
- Fuel 耗尽在部分 Windows 路径上可能 abort——生产技能应设合理 fuel，避免 tight loop。  
- 仍非浏览器 WASM；host 导入白名单固定。  

---

## 6. 关键文件

- `crates/takton-kernel/src/wasm_runtime.rs`
- `crates/takton-kernel/Cargo.toml` · 根 `Cargo.toml`
- `scripts/smoke_p2_integration.py`
- `backend/tests/kernel/test_p2_platform.py`
- `docs/WASMTIME_REPORT.md`
