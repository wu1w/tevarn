# Takton Core Runtime（主脑边界）

> L0–L3 认知/运行时；能力 pack 见 `tool_policy.py`。

## 主脑默认

- **Profile 默认 `coding`**：读写改 + web + meta `use_tool_pack`
- **不**默认暴露 manage/desktop/evolution（`ops` / `use_tool_pack` / `full`）
- Scene `dynamic` 仍可关键词加包

## 循环（L1）

- `IterationBudget` + grace 终答
- `TurnRetryState`：空正文 / 空工具名 / thrash / 429…
- `tool_result_contract` 统一截断
- 工具后禁止空白终答

## Workspace 契约（L3）

会话组装时注入（`workspace_contract.py`）：

| 文件 | 行为 |
|------|------|
| AGENTS.md | 有则截断注入；无则 `[missing]` |
| SOUL.md | 同上 |
| USER.md | 同上 |
| TOOLS.md | 同上（使用约定，不决定工具是否存在） |

另：`IDENTITY.md` 等人设仍由 `file_context.load_workspace_persona_bundle` 加载。

查找根：`TAKTON_FILE_BROWSER_ROOT` / 项目根 / cwd（见 `permissions.detect_project_root`）。

## Tool hooks（L3）

`ToolRegistry.execute`：

1. `before_tool_call`（可 block / 改参）
2. 权限检查
3. 执行（剥离 `_checkpoint*` meta）
4. `after_tool_call` + normalize

内置：`agent_file_checkpoint=true` 时，写工具前快照到 `.takton/checkpoints/<ts>/`。

注册：

```python
from backend.agent.tool_hooks import register_before_tool_call, BeforeHookResult
```

## 配置

| key | default | 含义 |
|-----|---------|------|
| agent_tool_profile | coding | coding/assistant/ops/dynamic/core/full |
| agent_file_checkpoint | true | 写前快照 |
| agent_auto_cluster | false | 禁止默认拆主脑 |
| agent_empty_reply_retries | 2 | 空正文重试 |
| agent_tool_repeat_max | 3 | 同参熔断 |

## Bench

```bash
set -a; source /opt/hermes-workspace/.secrets/bench_llm.env; set +a
.venv311/bin/python scripts/bench_agent/run_bench.py --models local-llm,kimi
```

## 非目标（主脑不做）

- 默认 80 工具 schema
- 无 bench 堆平台功能
- 把 gateway 全通道抄进默认环
