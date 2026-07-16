# 使用日志 / SFT 语料收集

## 开关

- **默认关闭**
- 设置页 → **数据与隐私** →「收集使用日志（SFT 语料）」
- 键名：`sft_usage_log_enabled`（`true` / `false`）
- 环境变量覆盖：`TAKTON_SFT_USAGE_LOG_ENABLED=1`
- 目录覆盖：`TAKTON_SFT_CORPUS_DIR=...`

## 问号说明（产品文案）

> 此功能开启后，Agent 将会自动收集用户指令和运行轨迹数据，所有数据均将以 SFT 语料的形式存在本地路径  
> `{TAKTON_HOME}/sft_corpus`（或 `data/sft_corpus`）

## 产出文件

| 文件 | 用途 |
|------|------|
| `sft_YYYY-MM-DD.md` | 人可读，便于导出检查 |
| `sft_YYYY-MM-DD.jsonl` | 每行一条 messages，便于训练脚本 |

敏感字段（api_key / token / password 等）会做简单脱敏。

## API

`GET /api/settings/sft-corpus` → `{ enabled, path, help, files }`
