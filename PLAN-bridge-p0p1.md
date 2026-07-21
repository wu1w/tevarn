# PLAN — Takton P0(Bridge) + P1(agent 层) 施工计划

日期：2026-07-21 · 执行：小白 · 来源：用户指令「P2先不做，把takton的P0+P1做一下」

## 现状盘点（Recon 结论）

**Bridge 服务端已被此前工作实现**（`backend/api/routes/bridge.py`，576 行，9 端点全，已注册）：
- ✅ `/health` `/models` `/chat/completions` `/skills` `/tools` `/tools/invoke` `/mcp` `/rag/search` `/settings`
- ✅ chat/completions 已复用 K4 修复点 `LLMServiceFactory.get_service_for_snapshot(snap)`（第 199-201 行），K4 坑未重引入
- ✅ tools/invoke 真实三级分发（unified registry → skill registry → DB tool），无桩
- ❌ **零测试覆盖**（backend/tests/ 与 tests/ 均无 bridge 测试）
- ❌ **无独立 Bearer token 机制**：所有端点走 `get_current_user`（Desktop 用户认证）。契约建议 Bearer；本机 loopback 场景可接受，但需确认 Code 侧如何拿到用户态

## P0 — Bridge 收尾（验证 + 鉴权策略 + 测试）

- [ ] T1 bridge 端点测试 `backend/tests/test_bridge_api.py`：
      health / models / chat(带 session_id 走 snapshot 路径，mock LLM) / tools/invoke(命中+未命中) / rag/search。
      用 FastAPI TestClient，外部依赖全 mock，不真打 LLM。
- [ ] T2 鉴权策略确认：向用户确认 Code 侧认证方式（loopback 直连 or Bearer token），按结论加可选 Bearer 校验。
- [ ] T3 真机冒烟：起 backend，curl 9 端点验证 200/结构（LLM 路径用 LOCAL 或 mock）。

## P1 — agent 层剩余坑清零

- [ ] T4 **压缩风暴熔断**（K-新坑）：读 `backend/agent/context*.py` 压缩层，加 ThrashingGuard
      （180s 内 ≥3 次 hard compact → 熔断只 micro，冷却 300s），抄 takton-code `ThrashingGuard` 语义。
- [ ] T5 **headless input 阻塞审计**：desktop agent `execute_task` 遇 ask 是否卡死？
      改 headless 下 ask→deny/cancel 并告知模型（抄 Grok headless 语义）。
- [ ] T6 **sanitize 调用链守护测试**（K2 防复发）：断言每次 LLM 调用必经 sanitize 层。
- [ ] T7 **system prompt 模型中立性检查**：确认经网关到非 Claude 模型无身份污染。

## 验收纪律

- 每项以测试循环收尾；攒一批验证完一起 commit+push（用户偏好，不逐小步推）。
- 不 bump 版本号、不动 P2。
- 验收要真命令输出，不编造。

## 待用户确认项

1. **Bridge 鉴权**：Code 侧如何认证？A) loopback 直连（现状 get_current_user 即可，需 Code 持有 desktop session token）
   B) 独立 Bearer token（`TAKTON_BRIDGE_TOKEN` 环境变量，契约推荐）。默认按 B 预留接口、A 现状可用。
