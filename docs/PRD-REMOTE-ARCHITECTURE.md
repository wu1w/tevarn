# Takton 远程设备架构 PRD

> 版本：v1.0（归档）  
> 日期：2026-07-15  
> 状态：Phase 1 MVP 开工  
> 目标：跨设备 Agent 集群 — 任意 UI 操作任意设备上的真实文件/命令

完整原文见会话附件；此处保留决策与 Phase 1 裁剪。

## 定位

自托管多机 Agent 工作台：控制面（Takton 后端）+ 每台设备 `takton-agent`。  
不主打云沙箱写 PR；主打真环境、多设备、可自建。

## 传输

L1 局域网 → L2 NetBird P2P → L3 VPS/TURN（Phase 1 **只做 L1**）

## Phase 1 默认决策（未再拍板时采用）

| 项 | 默认 |
|----|------|
| 控制面 | **当前 Takton 后端**（可配置；日后可指 m920x） |
| Desktop | 可当控制面，也可只跑 agent |
| 发现 | **手动 host:port + 配对 token** 为主；mDNS 后补 |
| 能力 | WS + `file.list` / `file.read` + 受限 `exec.run` |
| 安全 | root 沙箱、token、exec 超时/黑名单；写文件/PTY 延后 |
| @device | Phase 1.5：对话解析 `@name` 路由 exec |

## 验收（L1）

1. 本机/局域网启动 `takton-agent`，带 token  
2. 控制面登记设备并 `ping` 成功，延迟可见  
3. 可 list/read root 下文件  
4. 可 exec 白名单内短命令  
5. 对话 `@设备名 命令` 返回结果卡片（文本）

## 明确不做（Phase 1）

- NetBird / TURN / 一键买云  
- 远程写文件、PTY 全终端  
- 浏览器插件 agent-lite  
