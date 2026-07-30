# 包 / 技能分发（Phase 5.3 生态最小面）

> 不自建市场。兼容 **agentskills.io** 风格清单（distiller 已兼容）。

## 安装已有包

- API：`POST /api/packages/install`（见 OpenAPI）
- 实现：`backend/packages/publisher.py`
- 前端：Market / Skills 商店页

## 发布 URL 流程（维护者）

1. 打包 zip：顶层含 `SYSTEM.md` / `skill.yaml` 等（见 publisher 校验）
2. 托管到任意 HTTPS URL 或本地路径
3. 客户端：安装 URL 或上传 zip
4. 冲突：同名包 409，可 force 覆盖（视 API）

## 与 Evolution 关系

- 自产技能走 Evolution draft → **replay** → apply → 可选 packages 导出
- 手动 Market 安装是运营路径，成长主路径仍是审批 + Evolution

## 非目标（0.4.10 / 0.7 前）

- 自建中央技能市场
- 多租户计费
