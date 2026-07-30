# 架构审计报告 v4

> 审计时间：2026-07-30 21:13 CST  
> 工作区：`E:\项目\takton-alpha`  
> 审计工具：Python 文件扫描 + glob + grep（dry-run 方式）

---

## Part A — API 路由注册完整性

### 注册机制

`backend/api/routes/__init__.py` 提供 `register_routes(app, prefix)` 函数，通过 `app.include_router()` 注册所有路由。

### 路由文件统计

| 指标 | 数量 |
|------|------|
| `backend/api/routes/*.py` 文件总数 | **48**（含 `__init__.py`） |
| 路由模块文件（排除 `__init__.py`） | **47** |
| `__init__.py` 显式 import 的模块 | **46** |
| `app.include_router()` 调用数 | **47**（含 `cluster.ws_router` + `health`） |

### 注册方式

- **46 个模块**：在 `from . import (...)` 块中显式导入，再通过 `app.include_router(xxx.router)` 注册
- **health**：在 `register_routes()` 函数体内通过 `from .health import _health_router` 导入（避免路由前缀双重叠加）
- **cluster**：额外注册了 `cluster.ws_router`（WebSocket 路由，单独挂载因 auth 依赖限制）

### 完整性判断

| 检查项 | 结果 |
|--------|------|
| ✅ 所有路由文件都已注册 | 47/47（含 health 函数内导入） |
| ✅ 所有 import 的模块都有对应文件 | 46 个 import + health = 47 个文件均存在 |
| ✅ 无幽灵 import（import 了不存在的模块） | 无 |
| ✅ 无未注册的路由文件 | 无 |

> ⚠️ **注意**：`__init__.py` 中 import 块与 `include_router` 调用存在一对多对应（`cluster` 出现 2 次 router 调用：HTTP + WS），但所有模块名都匹配，无遗漏。

---

## Part B — 测试套件现状

### 测试文件分布

| 指标 | 数量 |
|------|------|
| `tests/**/*.py`（项目根） | **0**（不存在） |
| `backend/tests/**/*.py` | **143** 个文件 |
| 测试代码总行数 | **19,441** 行 |

### 测试子目录结构

| 目录 | 文件数 | 说明 |
|------|--------|------|
| `backend/tests/` (根) | ~118 | 主测试文件 + conftest.py |
| `backend/tests/kernel/` | ~22 | 内核相关测试 |
| `backend/tests/security/` | ~5 | 安全测试 |
| `backend/tests/tools/` | ~4 | 工具测试 |
| `backend/tests/agent/` | ~2 | Agent 测试 |

### Top 20 最大测试文件

| 行数 | 文件 |
|------|------|
| 615 | `kernel/test_shared_store.py` |
| 335 | `kernel/test_workforce_06.py` |
| 334 | `test_durable_run.py` |
| 333 | `test_working_mode.py` |
| 331 | `test_agent_contract.py` |
| 322 | `test_bridge_api.py` |
| 322 | `test_agent_computer.py` |
| 315 | `kernel/test_product_spine_046.py` |
| 313 | `kernel/test_crew_memory.py` |
| 294 | `test_p1_night_roadmap.py` |
| 288 | `kernel/test_audit_bugfix_loop.py` |
| 277 | `test_subagent_runner.py` |
| 275 | `test_orphan_tool_messages.py` |
| 272 | `kernel/test_evolution_07.py` |
| 271 | `kernel/test_stage23.py` |
| 264 | `test_cluster_persistence.py` |
| 251 | `test_h0_honesty.py` |
| 241 | `test_file_tools_contract.py` |
| 233 | `test_tool_parallel.py` |
| 228 | `kernel/test_phase2_gate.py` |

### pytest dry-run 结果

```
❌ FAILED — ImportError: No module named 'sqlalchemy'
```

**原因**：`conftest.py` 依赖 `sqlalchemy.ext.asyncio`（`AsyncSession`, `create_async_engine`），当前环境未安装 `sqlalchemy`。这是 **环境依赖缺失**，非测试代码问题。`requirements.txt` 应包含该依赖。

---

## Part C — 架构变更统计

### TODO / FIXME / HACK / XXX 标记

| 文件 | 标记类型 | 内容 | 评估 |
|------|----------|------|------|
| `agent/system_prompt.py:109` | TODO（误匹配） | `"temporary TODO state to memory..."` 是文档字符串内容 | ⚪ **误报** |
| `tests/kernel/test_stub_fill_p0p2.py:18` | 测试断言 | `assert "TODO(cron-hook)" not in src` | 🟢 测试确保 stub 已清除 |
| `tests/kernel/test_stub_fill_p0p2.py:24` | 测试断言 | `assert "TODO: 实现取消逻辑" not in src` | 🟢 测试确保 stub 已清除 |
| `tests/kernel/test_stub_fill_p0p2.py:31` | 测试断言 | `assert "TODO: 清除数据库中的权限" not in src` | 🟢 测试确保 stub 已清除 |

**有效 TODO/FIXME 数：0**（4 个匹配均为误报或测试断言）

### NotImplementedError 统计

| 分类 | 数量 | 说明 |
|------|------|------|
| `repositories/` 抽象方法 | ~95 | **抽象基类方法**，子类按需实现 |
| `services/embedding/interface.py` | 2 | 接口定义 |
| `services/image/interface.py` | 1 | 接口定义 |
| `services/llm/interface.py` | 2 | 接口定义 |
| `services/rag/interface.py` | 4 | 接口定义 |
| `services/reranker/interface.py` | 1 | 接口定义 |
| `skills/base.py` | 1 | 基类方法 |
| `tools/base.py` | 1 | 基类方法 |
| `tools/builtins/core_tools.py` | 1 | 运行时错误分支 |
| **合计** | **104** | — |

> ⚠️ **评估**：104 个 `NotImplementedError` **绝大多数（~95%）是抽象基类/接口定义的占位方法**，属于正常设计模式（Repository 模式 + Service 接口）。并非"未完成功能"，而是"子类未覆盖时的防御性异常"。

### backend/ 子目录清单（25 个）

| 目录 | 职责 |
|------|------|
| `adapters/` | 外部适配器 |
| `agent/` | Agent 核心逻辑 |
| `api/` | FastAPI 路由层 |
| `computer/` | 计算机操作/桌面能力 |
| `content/` | 内容处理 |
| `core/` | 核心基础设施 |
| `evolution/` | 自进化系统 |
| `integrations/` | 第三方集成 |
| `interfaces/` | 接口定义 |
| `kernel/` | 内核调度 |
| `mcp_hub/` | MCP 协议中心 |
| `migrations/` | 数据库迁移 |
| `models/` | 数据模型 |
| `packages/` | 包管理 |
| `project/` | 项目配置 |
| `repositories/` | 数据仓库层 |
| `runtime/` | 运行时管理 |
| `schemas/` | API Schema |
| `scripts/` | 脚本工具 |
| `services/` | 业务服务层 |
| `skills/` | 技能系统 |
| `static/` | 静态资源 |
| `tests/` | 测试套件 |
| `tests_manual/` | 手动测试 |
| `tools/` | 工具系统 |
| `workspace/` | 工作区管理 |

---

## 总结：架构大改后的整体完整度评估

### 量化指标

| 维度 | 评分 | 说明 |
|------|------|------|
| **路由注册完整性** | 🟢 **100%** | 47 个路由模块全部注册，无遗漏、无幽灵 import |
| **测试覆盖率（文件）** | 🟡 **中等** | 143 个测试文件 / 19,441 行，但 dry-run 因环境依赖缺失失败 |
| **未完成标记** | 🟢 **优秀** | 有效 TODO/FIXME 数为 **0** |
| **未实现功能** | 🟡 **需关注** | 104 个 `NotImplementedError`，其中 ~95% 为抽象接口占位（正常），~5 个分散在 `tools/builtins/core_tools.py` 等运行时路径 |
| **架构分层** | 🟢 **清晰** | 25 个子目录，分层明确：API → Services → Repositories → Models；独立的 kernel/evolution/skills/tools 模块 |

### 关键发现

1. ✅ **路由层完备**：架构大改后所有路由文件已同步注册，`register_routes()` 逻辑清晰
2. ⚠️ **测试环境依赖缺失**：`conftest.py` 依赖 `sqlalchemy`，当前 Python 环境未安装，pytest 无法 dry-run。需确认 `requirements.txt` 是否包含该依赖
3. ✅ **代码卫生良好**：无残留 TODO/FIXME/HACK 标记，stub 清除检查测试到位
4. ⚠️ **Repository 层抽象方法未实现**：`repositories/` 下 ~95 个 `NotImplementedError` 是设计意图（抽象基类），但应确认有对应子类覆盖了生产路径上的方法
5. 📊 **测试规模**：143 个文件 / 19K 行，平均 136 行/文件，结构合理

### 建议

| 优先级 | 建议 |
|--------|------|
| 🔴 P0 | 安装测试依赖（`pip install sqlalchemy`），验证 pytest collection 通过 |
| 🟡 P1 | 检查 `repositories/` 子类覆盖率，确认生产路径无 `NotImplementedError` 泄漏 |
| 🟢 P2 | 补充 `backend/tests/` 根级测试与 `backend/*/` 模块的一对一覆盖率统计 |
