# Tevarn 0.5.3-alpha · 2026-08-02

本版本继续面向本地优先、单用户的个人 AIOS/Agent 工作站，重点收口治理正确性、不可信内容边界与桌面使用体验。

## 主要修复

- 非法工作模式改为 fail-closed，固定回退 `cautious`。
- 远程 Agent package 默认要求可信 SHA-256，并统一安全下载、重定向复核和资源预算。
- Electron 锁定应用 origin、校验高权限 IPC、限制外链协议，并为桌面操作加入主进程原生确认凭据。
- DOCX 预览改用 DOMPurify，拦截危险元素和链接。
- Rust scheduler stats 恢复 ABI v1 顶层计数字段，同时保留嵌套结构。
- 未实现的 Workflow Loop 从编辑器移除；旧工作流遇到 Loop 明确失败，不再静默漏处理。
- 知识库导入、ZIP 展开和 bcrypt 密码长度增加明确边界。
- 修复未知任务来源被误判为主聊天而静默放行的问题。

## 前端与工程质量

- 修复中文聊天提示语言反转、输入框可访问名称、小字号和低对比度问题。
- 通过 960×640 Electron 最小窗口以及亮/暗主题实测，主要页面无横向溢出。
- 恢复 TypeScript 生产门禁，迁移 Next 16 `proxy` 约定并修复 Electron 编译脚本。
- 版本权威统一为 `0.5.3-alpha`。

完整审计与验证结果见 `.audit-report/CODE_AUDIT_REPORT_LOCAL_AI_OS_2026-08-02.md`。
