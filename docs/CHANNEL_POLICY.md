# 渠道策略（Phase 5）

## 冻结声明

**0.4.10-alpha / 公开 0.7 前：不新增第 8 个及以后渠道适配器。**  
已有适配器（QQ / Telegram / Discord / Slack / 飞书 / 钉钉 / Signal / 企微等）可修 bug，不扩面。

## 入站安全（已实现）

| 控制 | 配置 | 默认 |
|------|------|------|
| 最大字符 | `channel_ingress_max_chars` | 32000 |
| 剥离 NUL | `channel_ingress_strip_nul` | true |
| 去重 | gateway `_seen_msgs` | TTL 300s |

超限：回复用户「消息被拒绝」，**不进 agent loop**。

## Webhook / 签名

- 各平台签名校验在 **adapter 配置**（token / secret）
- 生产暴露公网 webhook 时：必须配置平台密钥；配合非 loopback 关闭 `single_user_mode`
- 详见 `security_check` 与部署文档

## 解冻条件

外部用户 issue 明确需求 + 维护者评估后，按月迭代逐个开启。
