# Electron / NSIS 冒烟清单（Phase 5.1d）

> 版本：`0.4.10-alpha` · 优先级：**Win NSIS > Linux AppImage > mac**

## 命令

```powershell
# 仓库根
npm install
npm run dist:win
# 产出见 electron-builder 配置（通常 dist/ 或 release/）
```

## 冒烟步骤

1. [ ] 打包成功，生成 `Takton Setup *.exe`（或等价 NSIS）
2. [ ] 干净用户目录安装
3. [ ] 启动后出现窗口 / 托盘
4. [ ] 单用户或登录成功
5. [ ] 发送一条 chat 有响应（需已配置 LLM）
6. [ ] 关窗行为符合 `electron/CANONICAL.md`（不误杀 runtime，若适用）
7. [ ] 卸载不残留关键密钥到错误位置（抽查）

## 已知限制

- 体积大（嵌入 Python 时）
- 杀软可能误报
- mac 公证 / Linux 桌面集成为软门禁

## 降级发版

若 NSIS 连续失败：公开以 **源码 + `start.py` + INSTALL.md** 为主路径，Release 标 beta。

## 记录

| 日期 | 机器 | 结果 | 备注 |
|------|------|------|------|
| 2026-07-30 | — | 清单就绪，待本机打包 | CI 可选后续加 |
