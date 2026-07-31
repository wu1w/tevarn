# 今日变更整体检查与测试报告

**日期**：2026-07-31  
**范围**：硬化 · 债 2–4 · wasmtime · 下一轮 T1–T10 · 加深轮  

---

## 1. 测试结果汇总

| 套件 | 结果 |
|------|------|
| `cargo test -p takton-kernel --lib` | **63 passed** |
| `cargo test -p takton-kernel --test abi_v1` | **4 passed** |
| pytest：next_round / resource_os / hardening / permission_fail_closed / sandbox_backends | **52 passed** |
| pytest：resource_os + p2_platform + r3_dedualize | **exit 0** |
| `scripts/smoke_p2_integration.py`（release host） | **PASSED**（ABI methods=**187**） |
| 关键 Python AST 解析 | **11/11 OK** |
| 路由/默认值 import 检查 | **OK** |
| Windows RSS 采样（复查后） | **OK**（~26–38 MB） |
| `ci_eval_gate.py`（无 artifact） | soft pass |

---

## 2. 静态审查结论

### 2.1 做得对的

- **双层 mediate**：`tool_gate` + Registry + tool_round 遗留路径；幂等 `_tool_gate_passed`
- **Court**：Rust 优先；host 在线失败 fail-closed，host 离线仍可 Python fallback
- **run_gate**：可配置必达；拒绝/超时硬失败
- **默认沙箱**：`DEFAULT_EXECUTION_MODE=sandbox`，无能力时 fail-closed 而非静默 local
- **wasmtime**：真执行 + hostcall 回退；smoke 验证 return=42
- **包市场**：签名扫描、远程 https 安装、quarantine
- **Electron**：`vendor/takton-kernel-host` + extraResources；查找路径已对齐

### 2.2 审查中发现并已修

| 问题 | 处理 |
|------|------|
| Windows `sample_rss_bytes_self` 恒为 `None`（psapi 绑定不当） | 改为 `K32GetProcessMemoryInfo` / psapi 双路径，复查 RSS 有效 |

### 2.3 残留风险 / 注意点（未改行为）

| 级别 | 项 | 说明 |
|------|-----|------|
| 中 | **默认 sandbox** | 无 Job/bwrap/seatbelt 的环境会直接报错；需显式 `agent_execution_mode=local` |
| 中 | **run_gate_required=true** | host 抖动时 run 创建会失败，不再静默跳过 |
| 中 | **court_rust_required** | host 在线但 decide 异常 → 全工具 deny（符合 harden，需监控） |
| 低 | **io_write_bytes 预扣** | 按参数 JSON 体量扣，非真实磁盘写入；大读小写也会扣 |
| 低 | **RSS 上报** | 采样本后端进程，非每个沙箱子进程树完整聚合 |
| 低 | **cgroup** | 默认关；Linux 无权限时静默 skip |
| 低 | **文档漂移** | `P2_COMPLETION_REPORT` 仍写「非 wasmtime」；应以 `WASMTIME_REPORT` 为准 |
| 低 | **ROADMAP P0 checkbox** | 仍是 `- [ ]`，工程已落地未回写勾选 |
| 信息 | **无 git 仓库** | 本目录非 git root，无法 `git diff` 统计 |

### 2.4 未跑满（刻意）

- 全量 `backend/tests`（时间长；CI 负责）
- 前端 e2e / TypeScript build
- 真 Electron 打包安装
- `takton_eval --run` 全量（依赖 host 占用与时间）
- 故意 fuel 耗尽 / 死循环测试（已知 Windows abort 风险）

---

## 3. 今日主变更面（功能）

1. **硬化**：tool_gate、Job/资源、workforce fail-closed  
2. **观测/Eval 周报**：weekly_report、API、CI gate  
3. **包市场**：scan/promote/catalog/远程 install  
4. **WASM**：wasmtime 28 + Cranelift  
5. **T1–T10**：Court/run_gate/默认沙箱/dashboard/collab/SDK pack…  
6. **加深**：RSS/cgroup、前端仪表盘与协作、vendor host、远程安装 UI  

---

## 4. 建议的后续（非阻塞）

1. 回写 ROADMAP/P2 文档勾选与 wasmtime 表述  
2. 给 `resource_report_rss` 加一条 Rust unit test  
3. CI 可选 job：`cargo test` + `smoke_p2`（带 host artifact）  
4. 前端 e2e 点一下 Kernel「仪表盘」Tab  

---

## 5. 总评

**工程状态：可合入级（对今日相关路径）**。  
核心 Rust/Python 单测与 P2 smoke 全绿；发现 1 处 Windows RSS bug 已现场修复并验证。  

上线前请确认：使用 **release/vendor host**（ABI 含 `resource_report_rss`，methods≈187），并知悉默认 **sandbox + run_gate 硬门** 的行为变化。
