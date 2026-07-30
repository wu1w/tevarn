# Phase 1–4 加压测试报告（2026-07-30）

> 环境：Windows · 项目 `.venv` · `TAKTON_TEST_MODE=1` · pytest 8.3.5 · pytest-xdist 3.8.0  
> 产物目录：`reports/p1-4-stress/`

## 1. 结论

| 维度 | 结果 |
|------|------|
| **顺序 / 多进程分片（每进程单 worker）** | **全绿** |
| **全量 `backend/tests` 顺序跑** | **1124 通过，0 失败，17 skip**（~172s） |
| **pytest-xdist `-n 4` 同库并行** | **不稳定**：setup 竞态 `sqlite3.OperationalError: table … already exists` |
| **业务断言失败（FAILED）** | **无**（xdist 问题均为 ERROR@setup，非断言失败） |

**稳定性判断**：Phase1–4 工程测试在「CI 默认单进程」路径下稳定；**当前测试库未为 xdist 做 per-worker DB 隔离**，加压并行会暴露 schema `create_all` 竞态。

---

## 2. 第一波：4 路并行分片（同时跑）

| 分片 | 范围 | tests | fail | err | skip | time | exit |
|------|------|------:|-----:|----:|-----:|-----:|-----:|
| **sec** | `backend/tests/security` | 94 | 0 | 0 | 0 | 2.1s | 0 |
| **p2** | phase1 concurrency / session merge / run unification / durable / phase2 gate | 44 | 0 | 0 | 0 | 6.5s | 0 |
| **p34** | memory_bus / permission / replay / crew_memory / llm_scheduler / project_python | 47 | 0 | 0 | 0 | 19.4s | 0 |
| **kernel** | `backend/tests/kernel` 全包 | 212 | 0 | 0 | 3 | 76.7s | 0 |

kernel skip：`test_stage23` ×3（本机无 bwrap）。

---

## 3. 第二波：扩展 + 全量 + xdist

| 套件 | 范围 | tests | fail | err | skip | time | exit |
|------|------|------:|-----:|----:|-----:|-----:|-----:|
| **wf-evo** | dual_run/budget · workforce · shared_store · evolution · p1_night | 59 | 0 | 0 | 0 | 37.5s | 0 |
| **full-backend** | `backend/tests` 全量（`-x`） | **1124** | **0** | **0** | **17** | **172s** | **0** |
| **xdist-p14**（与 full 同时） | P1–4 相关 + kernel，`-n 4 --dist loadfile` | 369 | 0 | **6** | 3 | 41s | **1** |

### xdist 单独复跑（无其它 suite 争用）

| 轮次 | exit | ERROR 数 | 现象 |
|------|------|----------|------|
| alone1 | 1 | 3 | 同上 schema 竞态 |
| alone2 | 1 | 3 | 同上 schema 竞态 |

典型错误：

```text
sqlite3.OperationalError: table kernel_checkpoints already exists
# 或 agent_identities already exists
# 发生在 pytest setup / create_all，多 worker 同时建表
```

受影响样例（每次不完全相同，属竞态）：

- `test_tool_auth.py::test_loopback_hosts_trusted[...]`
- `test_shell_injection.py::test_dangerous_commands_flagged[...]`
- `test_shared_store.py::test_put_get_process_roundtrip` / `test_charge_tokens_atomic`
- `test_stage23.py::test_scheduler_priority_order` / `test_scheduler_fifo_within_same_priority`

**上述用例顺序重跑：通过**（非逻辑回归）。

---

## 4. Skip 分类（全量 17）

| 原因 | 数量级 |
|------|--------|
| 本机无 `bwrap`（Linux 沙箱） | kernel stage23 + agent_computer |
| `takton-code` 未 checkout | bridge / batch3 |
| POSIX shell 语义（Linux CI 覆盖） | command_cwd_security |
| 桌面/打包产物未建 | desktop_agent / subagent_workflow_canvas |

均不阻塞 Phase1–4 工程关账。

---

## 5. 覆盖对照（DEV_PLAN P1–4）

| Phase | 测到的关键门禁 | 结果 |
|-------|----------------|------|
| **P1** | security 五件套、concurrency、session config merge | 绿 |
| **P2** | run_unification、durable_run、durable_run_recovery、phase2_gate | 绿 |
| **P3** | memory_bus、permission_*、crew_memory、memory_authority | 绿 |
| **P4** | replay_validator、llm_scheduler、evolution_07/08 | 绿 |
| **附带** | dual_run/budget、shared_store、project_python、kernel 全包 | 绿 |

---

## 6. 问题与修复（已落地）

| 问题 | 根因 | 修复 |
|------|------|------|
| xdist `-n 4` setup ERROR：`table … already exists` | worker 继承 controller 的 `TAKTON_DB_URL`（`setdefault` 不覆盖）→ 多进程同库并发 `create_all` TOCTOU | `conftest.py`：按 `PYTEST_XDIST_WORKER`+pid **强制**独立 DB；`create_all` 吞 `already exists` |
| 生产/多 worker 冷启动同类竞态 | `init_db` 裸 `create_all` | `database.py`：`_create_all_safe` |

**验证（修复后）**：xdist P1–4 套件 **连续 2 轮 EXIT=0**，无 ERROR（仅 bwrap 相关 3 skip）。

---

## 7. 一句话

> **Phase 1–4 工程测试全绿（1124）；压测暴露的 xdist SQLite 建表竞态已修，`-n 4` 双跑稳定。**
