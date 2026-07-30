# 默认零外部依赖（Phase 5.1a）

Takton **公开默认画像**：

```text
数据库     SQLite（takton.db / 配置 db_url）
Redis      关闭（agent_kernel_redis_shared=false, redis_url=""）
Qdrant     未配置（qdrant_url=""）→ RAG 本地/降级，不阻断启动
Kernel     单进程内存 + SQLite 持久化工单/Run
绑定      single_user_mode + loopback
```

可选增强（装得动之后再开）：

- `TAKTON_REDIS_URL` + `agent_kernel_redis_shared=true` → 多 worker  
- Embedding + `qdrant_url` → 向量 RAG  

验收：`backend/tests/test_phase5_zero_deps.py` + 安全回归。
