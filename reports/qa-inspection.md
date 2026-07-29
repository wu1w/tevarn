# QA 巡检报告 — 测试体系快速扫描

**日期**: 2026-07-26  
**工单来源**: crew_steward (api)

---

## 1. backend/tests/ 测试文件清单

| 文件 | 类型 |
|------|------|
| `backend/tests/__init__.py` | 包标记（空文件） |
| `backend/tests/conftest.py` | pytest fixtures 配置 |

**⚠️ 结论：backend/tests/ 下没有任何 `test_*.py` 文件。0 个实际测试。**

---

## 2. tests/ (根目录) 文件清单

| 文件 | 类型 |
|------|------|
| `tests/__init__.py` | 包标记（空文件） |
| `tests/conftest.py` | pytest fixtures 配置（旧版本） |

**⚠️ 同样没有任何 `test_*.py` 文件。**  
注：`pyproject.toml` 中 `testpaths = ["backend/tests"]`，`tests/` 未被 pytest 识别为测试目录。

---

## 3. e2e/ 目录

目录存在但为**空目录**，无任何文件（无 `.js`、`.ts`、`.py`、`.gitkeep`、`package.json` 等）。

---

## 4. conftest.py 测试框架配置分析

### backend/tests/conftest.py（主配置）
- **框架**: pytest 8.3.5 + pytest-asyncio 0.24.0, asyncio_mode = "auto"
- **DB**: SQLite + aiosqlite，使用进程级临时文件（非 `:memory:`），避免连接池污染
- **Fixtures**:
  - `prepare_test_database` (session scope, autouse): 建表/删表
  - `db_session`: 每测试独立 session + rollback
  - `client`: FastAPI TestClient with DB override
  - `settings`: 测试用 Settings 实例
- **环境变量**: JWT_SECRET, API_KEY, TAKTON_DB_URL, SINGLE_USER_MODE

### tests/conftest.py（旧版本）
- 使用 `:memory:` SQLite — 有已知问题（注释中标注了历史潜伏 bug）
- 使用 `DB_URL` 而非 `TAKTON_DB_URL`（Settings 的 env_prefix="TAKTON_"，裸 `DB_URL` 不生效）

**⚠️ 两份 conftest.py 存在重复配置，tests/conftest.py 是过时版本，保留会造成维护混乱。**

---

## 5. TODO / FIXME / skip / xfail 标记

**0 处。** 没有 test_*.py 文件，因此也不存在任何测试标记。

---

## 6. "招人→派活→执行→提权→日报" 主路径测试覆盖度

| 步骤 | 对应路由/模块 | 测试覆盖 | 状态 |
|------|-------------|---------|------|
| **招人** (注册/创建 Identity) | `backend/api/routes/auth.py` → `/auth/register` | ❌ 零测试 | 🔴 |
| **派活** (任务分配/Session 创建) | `backend/api/routes/sessions.py` + `tasks.py` → `/sessions/*` | ❌ 零测试 | 🔴 |
| **执行** (Agent Loop / Kernel) | `backend/agent/` + `backend/api/routes/kernel.py` → `/kernel/processes` | ❌ 零测试 | 🔴 |
| **提权** (Escalation 授权) | `backend/api/routes/kernel.py` → `/kernel/escalations` | ❌ 零测试 | 🔴 |
| **日报** (通知/审计) | `backend/api/routes/notifications.py` + `audit.py` | ❌ 零测试 | 🔴 |

**覆盖度: 0% — 主路径完全无测试。**

---

## 总结

| 指标 | 结果 |
|------|------|
| test_*.py 文件总数 | **0** |
| 测试用例数 | **0** |
| conftest.py 数量 | 2（其中 1 个过时） |
| e2e 文件数 | 0 |
| TODO/FIXME/skip/xfail | 0 |
| 主路径覆盖度 | **0%** |
| 有效路由模块数 | ~40+ |
| 有效 Repository 数 | ~25+ |
| Schema/Model 数 | ~30+ |

### 风险评估

🔴 **极高风险**: 整个项目有完善的测试基础设施（conftest、pytest 配置、fixture 体系），但**一个实际测试都没有**。所有 40+ 路由模块、25+ Repository、核心 Agent Loop/Kernel 完全依赖手工验证。

### 建议优先级

1. 🔴 **P0 — 冒烟测试套件**: 先为"招人→派活→执行→提权→日报"主路径写 5 个端到端测试（可用已有的 client fixture + TestClient）
2. 🔴 **P0 — 清理重复 conftest**: 删除 `tests/conftest.py`，统一用 `backend/tests/conftest.py`
3. 🟡 **P1 — 核心路由覆盖**: auth、kernel、sessions、sub_agents、notifications 各写 2-3 个基础 CRUD 测试
4. 🟡 **P1 — Repository 层**: 至少覆盖 UserRepository、TaskRepository、SessionRepository 的基本操作
5. 🟢 **P2 — E2E 框架搭建**: 为 e2e/ 目录引入 Playwright 或 httpx，覆盖跨模块流程
