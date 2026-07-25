"""Agent Computer（Phase 0.5.3）：隔离执行后端

- protocol: ExecutionBackend 协议 + ExecResult
- local_backend: 现状直跑（默认降级）
- bwrap_backend: bubblewrap 沙箱（Linux）
- manager: 按 agent_key 管理 per-agent computer（主 Agent 与子代理互不干扰）
"""
