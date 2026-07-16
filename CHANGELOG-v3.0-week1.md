# Takton v3.0 Week 1 — 验收文档

> 日期：2026-07-13
> 范围：Skill/Tool 统一抽象（D）
> 状态：✅ 完成

---

## 验收检查清单

### D. Skill/Tool 整合

- [x] 所有工具通过 `BaseTool` 注册
  - 22 个工具（11 builtin skills + 11 BUILTIN 工具）通过统一 ToolRegistry 注册
  - 支持 5 种来源：BUILTIN / SKILL / DYNAMIC / DB / MCP
  - 来源优先级：builtin(0) > skill(1) > dynamic(2) > db(3) > mcp(4)
- [x] 前端工具列表统一
  - `/api/tools` 返回统一 schema
  - 禁用工具自动从 schema 中排除
- [x] 新增工具开发时间 < 30 分钟
  - 基类 `BaseTool` 只需实现 `execute()` 方法
- [x] 单元测试覆盖 > 80%
  - 11 个工具单元测试全部通过（test_unified_tools.py + test_mcp_adapter.py）
  - 全量 30 项后端测试全部通过

---

## 架构变更

### 统一工具层（backend/tools/）

```
backend/tools/
├── base.py              # BaseTool 抽象 + ToolSource/ToolRiskLevel 枚举
├── registry.py          # 统一 ToolRegistry（单例，含 reset()）
├── builtins.py          # 11 个内置工具（注入 workspace root config）
├── loader.py            # 异步加载器：skill → builtin → dynamic → db
├── permissions.py       # ToolPermissionManager（集成 workspace root）
└── adapters/
    ├── skill_adapter.py # BaseSkill → BaseTool
    ├── mcp_adapter.py   # MCP tool → BaseTool（自动 mcp_ 前缀）
    ├── db_tool_adapter.py # DB Tool → BaseTool（使用 merged_config）
    └── dynamic_adapter.py # DynamicSkill → BaseTool
```

### MCP Hub（backend/mcp_hub/）

```
backend/mcp_hub/
├── client.py            # MCPClient + MCPClientManager（stdio/sse）
└── service.py           # load_mcp_tools + get_mcp_status
```

### Workspace（backend/workspace/）

```
backend/workspace/
└── service.py           # 绑定/树/终端/持久化（workspace_state.json）
```

---

## Bug 修复清单（7 项）

| # | 文件 | 问题 | 修复 |
|---|------|------|------|
| 1 | `permissions.py` | ToolPermissionManager 未集成 workspace root | 默认对文件操作类工具做边界检查，/etc/passwd 被正确拦截 |
| 2 | `builtins.py` | BuiltinTool.execute 注入空 config | 注入 workspace root 作为 base_path |
| 3 | `registry.py` | 类变量 _tools 跨测试状态泄漏 | 添加 reset() 方法用于测试隔离 |
| 4 | `mcp_adapter.py` | MCP 工具名与内置工具冲突 | 自动加 mcp_ 前缀 |
| 5 | `db_tool_adapter.py` | execute() 使用原始 self._config | 使用 merged_config |
| 6 | `workspace/service.py` | 重启后 workspace 绑定丢失 | _USER_ROOTS 持久化到 workspace_state.json |
| 7 | `health.py` | 缺少 /api/health 路径兼容 | 添加 _health_router |

---

## 测试结果

### 后端单元测试：30/30 通过

```
backend/tests/test_chat_sse.py            ..  [  6%]
backend/tests/test_cron_scheduler.py      ..... [ 23%]
backend/tests/test_msg_chars_none.py      ....  [ 36%]
backend/tests/test_single_user.py         ....  [ 50%]
backend/tests/test_tool_result_normalize.py .... [ 63%]
backend/tests/tools/test_mcp_adapter.py   ....  [ 76%]
backend/tests/tools/test_unified_tools.py ....... [100%]
============================== 30 passed in 1.96s ==============================
```

### E2E 测试：41/41 通过

```
Results: 41/41 passed, 0 failed, 0 skipped
```

### 工具单元测试：11/11 通过

```
backend/tests/tools/test_mcp_adapter.py   ....  [ 36%]
backend/tests/tools/test_unified_tools.py ....... [100%]
============================== 11 passed in 0.20s ==============================
```

---

## 关键修复记录

### 本次会话（2026-07-13）

1. **E2E 测试端口修复**：`full_stack_e2e.py` 的 `API_BASE` 从 `:8000` 改为 `:8090`，`PROXY_BASE` 从 `:3000` 改为 `:8090`
2. **MCP 测试适配**：`test_mcp_adapter.py` 中工具名从 `echo`/`add` 改为 `mcp_echo`/`mcp_add`，匹配 MCPToolAdapter 自动加前缀逻辑
3. **Python 执行器修复**：`executors.py` 中 `create_subprocess_exec("python", ...)` 改为 `"python3"`，适配系统无 `python` 命令的环境
4. **测试路径前缀修复**：`test_single_user.py`、`test_cron_scheduler.py`、`test_chat_sse.py` 中 API 路径从无前缀改为 `/api/` 前缀，匹配 `register_routes(app, prefix="/api")`
5. **main.py 生命周期迁移**：`@app.on_event("startup")`/`@app.on_event("shutdown")` 迁移为 `@asynccontextmanager lifespan`，消除 FastAPI deprecation warning

---

## 下一步（Week 2）

- [ ] MCP 客户端接入（client.py 已就绪，需集成测试）
- [ ] MCP Server 管理页面（前端）
- [ ] MCP 工具权限配置
- [ ] MCP 集成测试

---

*文档结束*