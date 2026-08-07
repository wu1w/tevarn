# Takton 0.5.8-alpha · 2026-08-07

**分支**：`feature/agent-kernel`  
**版本权威**：`backend/VERSION` · `frontend/lib/appVersion.ts` · root/`frontend` `package.json`

## 本版要点

### 思考链展示（Grok / 推理模型）
- 原生 `reasoning_delta` 流式推送为可折叠 `<thinking>` 块（对齐 Claude / Codex 体验）
- 落库与终答保留思考块；空回复判定忽略 thinking-only

### 工具结果 / 截断（桌面）
- 修正过狠的 spill：`SPILL_THRESHOLD` 与 per-tool soft budget 对齐
- 大结果 envelope：丰厚 head+tail preview + `result_load` 分页提示
- `result_load` 支持 `offset` / `max_chars` 分页
- 不再出现「天气 JSON ~800 字被截成 handle」导致模型空转重跑

### Web Search（桌面）
- 补齐 `ddgs` 依赖与嵌入式 Python `._pth` / `sitecustomize`（忽略 `PYTHONPATH` 的坑）
- free_search 兼容 `ddgs` / 旧包名；Bing 优先；安装失败有明确 hint
- electron 启动时确保 user site-packages 可导入；`bcrypt` 钉在 4.0.x（避免 passlib 崩）

### Kernel
- Rust `result_store` 默认阈值/预览上调；handle 文案引导 `result_load` 而非重跑工具

### 手机端（APK 同步发布）
- 本机 LLM：工具截断改为 head+tail，放宽 soft/hard 压缩阈值
- `web_search`：Bing HTML 兜底、足够结果时跳过慢后端、失败 hint
- 仍保持轻量：无 spill 重机制

## 安装包

| 产物 | 说明 |
|------|------|
| Windows NSIS / portable | 桌面一键安装 |
| Android APK | 手机端（本机 LLM + 远端 Agent） |

## 升级注意
- 桌面：若搜索仍失败，确认 `%APPDATA%/takton/python-packages` 含 `ddgs`，或重启一次让 electron 装依赖
- 推理模型建议 `reasoning_effort` 用 medium（max 会长时间「思考中」）
