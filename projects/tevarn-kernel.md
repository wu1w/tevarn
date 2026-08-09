# 项目：tevarn-kernel（agent-kernel）

## 元信息
- 类型：产品主线
- 分支：feature/agent-kernel
- 状态：进行中
- 更新：2026-08-02

## 目标
个人 Agent 终端：编制 / 审批 / 内核 / 用量可观测；Windows 桌面与源码开发路径一致。

## 近期交付
- 用量页 `/usage`（供应商×模型筛选）
- force 删会话 cancel + clear grants；result_load process 绑定
- session grants 落盘 TTL + 孤儿清理；evolution 归属过滤
- kickedByPeer 禁用输入；streamSessionStore 上限

## 路径
见仓库根 / APPDATA workspace 的 `memory.md`「路径权威」表。

## 待办
- [ ] gh auth 非交互确认
- [ ] Qdrant / SMTP（后置）
