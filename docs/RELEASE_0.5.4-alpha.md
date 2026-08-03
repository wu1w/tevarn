# Takton 0.5.4-alpha · 2026-08-03

**分支**：`feature/agent-kernel`  
**版本权威**：`backend/VERSION` · `frontend/lib/appVersion.ts` · root/`frontend` `package.json`

## 本版要点

### ChatGPT OAuth / 订阅配额
- OpenAI ChatGPT OAuth（PKCE）+ Codex 兼容代理，走 Plus/Pro 订阅额度（非 sk 平台计费）
- 出站 HTTPS 尊重系统代理（`HTTPS_PROXY` / `TAKTON_HTTPS_PROXY`），修复 country 限制与死代码端口
- 模型面暴露 GPT-5.6 系列

### Kernel / 工具
- CEO/管家默认 kernel 令牌全开（`apply_intent` / escalate `*`），不再因 coding profile 收窄导致「令牌范围不含 okr_goal/manage_goal」
- 工具映射补齐 `okr_goal` / `manage_goal` / `autopilot`；coding profile 含目标工具
- ChildProc 配额与 `process` 不计费路径、JobBackend 多 work root
- core tools 3-arg executor 修复

### 用量与缓存
- **持久化用量账本** `%APPDATA%/takton/data/usage_ledger.json`（kernel host 重启不清零）
- 按供应商 family + model 累计；缓存命中 dual-write durable
- Anthropic prompt caching：`anthropic-beta` 头 + 稳定 tools 排序
- OpenAI-compatible：tools 排序/参数键序稳定、`prompt_cache_key` 派生
- RAG/Wiki 注入改为尾部 system，避免破坏自动前缀缓存

### UI / 产品
- 用量页文案与 durable ledger 对齐
- 目标页 / 设置模型 OAuth 相关接线

## 升级注意
- 旧会话若仍见令牌拒绝：发一条新消息触发新 process 全开即可
- 用量历史从本机 `usage_ledger.json` 读取；清零可删该文件
