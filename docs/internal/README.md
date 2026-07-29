# Takton Alpha · 内部文档索引

> 产品线：alpha 私有 · Personal Agent OS（数字班子）  
> 版本锚点：`0.4.6-alpha` + 0.5/0.6 预览 · OS 化路线见 `ROADMAP_AIOS_OS_FULL.md`

---

## 必读（按角色）

| 你是… | 先读 |
|--------|------|
| 新人上手开发 | [DEV_HANDBOOK.md](./DEV_HANDBOOK.md) → [ARCHITECTURE.md](./ARCHITECTURE.md) → [TOPOLOGY.md](./TOPOLOGY.md) |
| 改内核 / 权限 / 工单 | [ARCHITECTURE.md](./ARCHITECTURE.md) · [KERNEL_PLAN.md](../KERNEL_PLAN.md) · [concepts.md](./concepts.md) |
| 做 OS 化 / 启动模型 | [ROADMAP_AIOS_OS_FULL.md](./ROADMAP_AIOS_OS_FULL.md) · [TOPOLOGY.md](./TOPOLOGY.md) |
| **到 AIOS 终点** | **[ROADMAP_TO_AIOS.md](./ROADMAP_TO_AIOS.md)**（产品+架构火车） |
| 产品 / 验收 | [concepts.md](./concepts.md) · [AIOS_OPERATOR.md](./AIOS_OPERATOR.md) · [CRASH_RECOVERY.md](./CRASH_RECOVERY.md) |
| 互操作 / 外部脚本 | [PROTOCOL.md](./PROTOCOL.md) |

---

## 文档地图

### 架构与 OS 化

| 文档 | 内容 |
|------|------|
| **[ARCHITECTURE.md](./ARCHITECTURE.md)** | 项目架构：分层、目录、依赖、模块职责（现状+目标） |
| **[TOPOLOGY.md](./TOPOLOGY.md)** | 拓扑：进程/部署/数据/事件/网络 |
| **[DEV_HANDBOOK.md](./DEV_HANDBOOK.md)** | 开发手册：环境、约定、改哪里、测试、反模式 |
| [ROADMAP_AIOS_OS_FULL.md](./ROADMAP_AIOS_OS_FULL.md) | 彻底 OS 化完整路线图（0.6→1.0） |
| [AGENT_OS_ARCHITECTURE.md](./AGENT_OS_ARCHITECTURE.md) | 与业界 Agent-OS 对照 |
| [ROADMAP_0.4.5_to_0.6.md](./ROADMAP_0.4.5_to_0.6.md) | 近程产品路线 |

### 产品与运维

| 文档 | 内容 |
|------|------|
| [concepts.md](./concepts.md) | 用户三词：员工 / 工单 / 审批 |
| [AIOS_OPERATOR.md](./AIOS_OPERATOR.md) | 操作手册与一周自检 |
| [CRASH_RECOVERY.md](./CRASH_RECOVERY.md) | 崩溃恢复预期 |
| [STORAGE.md](./STORAGE.md) | SQLite 权威 / Redis 可选 |
| [MEMORY_AUTHORITY.md](./MEMORY_AUTHORITY.md) | 记忆写入权威 |
| [EVOLUTION_NARRATIVE.md](./EVOLUTION_NARRATIVE.md) | 进化人批叙事 |

### 协议

| 文档 | 内容 |
|------|------|
| [PROTOCOL.md](./PROTOCOL.md) | 互操作协议 0.1（Agent Card · A2A-lite） |

---

## 一句话架构

```
Clients（Electron/Web/CLI）→ Adapters（FastAPI）→ Runtime（编制/工单）→ Kernel（控制面）→ SQLite
```

**Kernel 是主角；UI 是控制台。** 关窗 ≠ 关机（目标语义，见路线图 0.7+）。
