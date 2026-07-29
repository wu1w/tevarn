# 崩溃与重启预期（0.5.0 Durable）

## 默认存储

- **SQLite** 为工单 / 编制 / 会话权威（`docs/internal/STORAGE.md`）
- Redis 仅多 worker 可选

## kill 后端后

| 对象 | 预期 |
|------|------|
| pending 工单 | 仍在 DB，重启后 dispatcher 继续 claim |
| claimed 超时 | `reclaim_stale_claims` 回 pending |
| 达最大 attempts | status=`dead`，进死信台，可重放/丢弃 |
| 运行中 process | 标记 interrupted（不伪造存活） |
| 主人 1:1 会话 | SQLite 消息仍在 |

## 手测清单

1. 派 1 条工单 → 立即 kill uvicorn  
2. 重启 → 工单 5 分钟内 pending→claimed→done 或 dead  
3. 死信台可见 → 点重放 → 再次执行  

## API

- `GET /kernel/inbox/dead`
- `POST /kernel/inbox/{id}/requeue`
- `POST /kernel/inbox/{id}/discard`
- `GET /kernel/jobs/running`
