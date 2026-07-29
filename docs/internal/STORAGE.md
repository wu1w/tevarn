# Alpha 存储决策（0.4.6+）

> **拍板日期**：2026-07-28  
> **默认**：**SQLite 为权威**；**Redis 仅可选热共享接口**（多 worker）。

---

## 1. 结论

| 数据 | 权威存储 | 说明 |
|------|----------|------|
| Identity / Inbox 工单 / 进程档案 / 审批与进化 / 会话消息 | **SQLite**（`db_url`，默认 `./takton.db`） | 可离家、可备份、重启可恢复 |
| 多 worker 下 process tokens / 提权 claim / 事件热缓冲 | **Redis**（可选） | `agent_kernel_redis_shared=true` + `redis_url` |

**个人本机默认路径不要求安装 Redis。**  
未配置或 ping 失败时，kernel 回退单进程内存热状态；**业务表仍在 SQLite**。

---

## 2. 配置

```bash
# 默认（推荐个人 / AIOS 默认可离家）
# TAKTON_DB_URL=sqlite+aiosqlite:///./takton.db
# TAKTON_AGENT_KERNEL_REDIS_SHARED=false

# 多 uvicorn worker 时建议打开
# TAKTON_AGENT_KERNEL_REDIS_SHARED=true
# TAKTON_REDIS_URL=redis://127.0.0.1:6379/0
```

代码入口：

- SQLite：`backend.core.config.settings.db_url`
- Redis 工厂：`backend.kernel.shared_store.create_shared_store_from_settings()`  
  → 返回 `KernelSharedStore | None`（接口保留，默认 None）

---

## 3. 0.5.0 Durable 方向（在本决策下）

- Inbox 状态机、reclaim、死信：**写 SQLite**（`agent_inbox_items` 等）
- Dispatcher 启动扫描：依赖 SQL，不依赖 Redis
- Redis：仅当多 worker 需要跨进程 mediate/charge 时启用

---

## 4. 不做什么

- 不把工单权威迁到 Redis  
- 不把「可离家」做成「必须装 Redis」  
- 不删除 Redis 接口（`shared_store` 与配置开关保留）
