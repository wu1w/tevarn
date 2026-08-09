# Tevarn 0.5.1-alpha · 2026-08-01

**分支**：[`feature/agent-kernel`](https://github.com/wu1w/tevarn/tree/feature/agent-kernel)  
**主题**：Host/预算/权限稳定性 + LLM 设置可用性 + 默认工作方式

---

## 本版要点

### 1. Agent Kernel / Host

- Inbox claim 粘性、`set_budget` 绝对/抬升语义、资源释放与 ChildProc 并发租约
- Host 假死/重水合风暴：软失败 + 限速，避免 UI 误报「Host 不可用」
- 无头确认与权限 fan-out；CEO / steward 本地放行策略收紧可感知路径

### 2. 权限与工作方式

- 全局默认工作方式改为 **自动编辑**（工作区内改文件不问，命令仍确认）
- 权限静默 deny 时向用户侧 fan-out，避免「没弹窗却拒绝」

### 3. LLM 设置

- 首次填 Key 拉取模型：真正下拉可见列表；登记时缓存 models
- **思考强度**（off/low/medium/high/max）按模型保存并映射到各家 API
- 会话快照带上 `reasoning_effort`

### 4. 其它

- 工具 stderr 多编码解码（减少 GBK 乱码）
- 沙箱 `~/.tevarn` / `TEVARN_HOME` 路径可见性
- 前端 domain WS、Runtime 健康条、聊天滚动等体验修复

---

## 版本号

- `backend/VERSION` · 根 / `frontend` `package.json` · `pyproject.toml` → **0.5.1-alpha**
