# 下一轮工作计划：10 项有价值建议

**日期**：2026-07-31  
**状态**：✅ 本轮已落地（2026-07-31）  
**依据**：三份外部 LLM 意见交集 + ROADMAP §2.2 未打穿项

---

## 1. 范围

| ID | 项 | 目标 | 验收 |
|----|-----|------|------|
| **T1** | Court path/secret 权威在 Rust | Python 仅 fallback；策略可观测 | `decide_tool` 优先 Rust；secret/path 单测 |
| **T2** | 调度唯一事实源 | run_gate 失败不静默放行（可配置） | `run_gate_required` + 指标 |
| **T3** | 资源硬限加深 | io 字节 charge + 全局资源快照 | tool 大结果扣 io；API 可见 |
| **T4** | 沙箱默认覆盖率 | 默认执行环境更严 + 覆盖率指标 | default=sandbox/auto 策略 + `/sandbox/coverage` |
| **T5** | host 便携分发 | 不依赖 Electron 杀进程 | `scripts/package_portable_kernel.*` |
| **T6** | 观测聚合面板 | 一页 API 聚合 | `GET /api/kernel/dashboard` |
| **T7** | 包市场远程源雏形 | 可配置 catalog URL + 扫描 | market remote list/scan |
| **T8** | Agent SDK 发布形态 | pack 校验 + 文档 | `takton_sdk_pack.py` exit 码 |
| **T9** | collab 控制面 API | HTTP 打穿 collab_* | `/api/kernel/collab/*` |
| **T10** | Eval 周报 CI 门禁 | 脚本 + workflow 可选 job | `ci_eval_gate.py` |

**明确不做本轮**：重型前端大改、公有 SaaS、改 OS 内核、粗暴杀进程装 Electron。

---

## 2. 实施顺序

```text
T1 Court → T4 沙箱默认 → T2 run_gate 硬门 → T3 资源
    → T6 观测 → T9 collab API → T7 远程包 → T8 SDK → T5 便携包 → T10 CI
```

---

## 3. 配置开关（新增/强化）

| 键 | 默认 | 含义 |
|----|------|------|
| `agent_kernel_run_gate_required` | true | run_gate 失败则拒绝 run |
| `agent_execution_mode` | `sandbox`（从 auto 收紧） | 默认更隔离 |
| `agent_package_market_url` | "" | 远程 catalog JSON |
| `agent_court_rust_required` | true | host 可用时禁止 silent Python court |

---

## 4. 回滚

各开关可回退：`agent_kernel_run_gate_required=false`、`agent_execution_mode=auto`、`agent_court_rust_required=false`。

加深轮见 `docs/DEEPEN_ROUND.md`（cgroup/RSS、前端仪表盘、远程安装、Electron vendor host）。

---

## 5. 本轮交付对照

| ID | 交付 | 文件 |
|----|------|------|
| T1 | Court：host 在线时 Rust 失败 fail-closed；secret globs 加深 | `permission_court.py`, `court.rs` |
| T2 | run_gate_required；失败不静默 | `loop.py`, `config.py` |
| T3 | 大参数 `io_write_bytes` charge | `tool_gate.py` |
| T4 | 默认 `execution_mode=sandbox` + `/sandbox/coverage` | `working_mode.py`, `kernel.py` routes |
| T5 | 便携 host 打包脚本 | `scripts/package_portable_kernel.ps1` |
| T6 | `/api/kernel/dashboard` | `kernel.py` |
| T7 | 远程 catalog URL（https only） | `market.py`, config |
| T8 | sdk pack + zip | `takton_sdk_pack.py`, `agent-sdk.md` |
| T9 | collab HTTP API | `kernel.py` collab_* |
| T10 | `ci_eval_gate.py` + backend-ci step | scripts + workflow |

```powershell
python -m pytest backend/tests/kernel/test_next_round_10.py -q
python scripts/ci_eval_gate.py
# optional after cargo build:
# .\scripts\package_portable_kernel.ps1
```
