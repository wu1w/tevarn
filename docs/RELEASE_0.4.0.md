# Tevarn v0.4.0 · 新主线首个稳定版

**发布日期**：2026-08-09  
**分支**：`main`（由 `feature/agent-kernel` 晋升）  
**代号**：Tevarn 新主线 · 首个稳定版本

## 亮点

- **品牌**：Takton → **Tevarn**（桌面 / 手机 / 安装包 / 环境变量 `TEVARN_*`）
- **本地优先 AIOS**：Rust Kernel Host · AI 员工 · 工单 · 权限法院 · 用量账本
- **Windows 稳定性**：`backend.win_boot` 强制 Selector 事件循环，降低 Codex 多路 SSE 无栈退出
- **桌面壳**：异常退出自动拉起后端、崩溃面包屑、Codex isolate 子进程
- **双端**：Electron 工作站 + Flutter 安卓遥控

## 安装包

| 文件 | 说明 |
|------|------|
| `Tevarn-Setup-0.4.0-x64.exe` | Windows 安装版 |
| `Tevarn-0.4.0-win-portable.exe` | Windows 便携版 |
| `Tevarn-0.4.0-win-x64.zip` | 解压即用 |
| `Tevarn-Mobile-0.4.0.apk` | Android |

## 升级说明

- 旧数据目录 `~/.takton` / `%APPDATA%\takton` 可软迁移；新默认 `~/.tevarn` / `%APPDATA%\tevarn`
- 环境变量优先 `TEVARN_*`，仍兼容 `TAKTON_*`
- 历史 0.3.x Release 与 0.5.x-alpha 预览线：以本版 `main` + `v0.4.0` 为新主线

## 校验

安装后 health 应返回 `{"status":"ok","service":"tevarn-backend"}`，进程入口为 `python -m backend.win_boot`。
