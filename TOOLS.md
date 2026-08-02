# TOOLS.md — 工具与落盘约定

## 命令 / 进程
- 默认 shell：**Windows cmd**（`command` / Job 沙箱）
- 沙箱 workspace 根 = 当前绑定项目目录（源码开发时常为仓库根）
- 虚拟 home：`<workspace>\\.computers\\main\\home`
- `TAKTON_HOME` / computer 内 `.takton`：技能、evolution、部分运行态

## 技能 Skills
- 查找顺序以运行时注册表为准；磁盘常见于：
  1. `<repo>\\.computers\\main\\home\\.takton\\skills\\...\\SKILL.md`
  2. `%USERPROFILE%\\.takton\\skills`（若存在）
  3. 仓库内 `backend/skills/builtins`
- 进化技能：`evo_*`，与「自主进化」列表同步

## 记忆文件
| 文件 | 用途 |
|------|------|
| `memory.md` | 跨会话索引 |
| `memory_temp.md` | 会话缓存 |
| `memory/YYYY-MM-DD.md` 或 `memory-YYYY-MM-DD.md` | 按日短记忆 |
| `AGENTS.md` / `SOUL.md` / `USER.md` / `TOOLS.md` | 启动契约 |

查找根（`file_context`）：session workspace → env → **APPDATA takton/data/workspace** → cwd → 仓库根 → **`.computers/*/home`**

## 用量与缓存（只读观测）
- UI：`/usage`（侧栏「用量」）
- API：`GET /api/kernel/cost`、`GET /api/kernel/cache/metrics`
- 维度：provider family + model；**进程内累计**，host 重启清零
- 写入来自真实 LLM usage 回填，**禁止**手工 mock

## GitHub
- `gh` CLI 可能已安装；调用前用非交互方式确认 `gh auth status`（失败则说明未登录，勿强行 `auth login` 卡交互）
- 本仓库远程：`github.com/wu1w/takton` · 主开发分支常为 `feature/agent-kernel`
