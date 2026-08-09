<div align="center">

<img src="website/assets/logo.png" alt="Tevarn" width="96"/>

# TEVARN

### 你的个人本地 · 开源 AIOS 工作站

**Tevarn** 运行在你自己的电脑上：本地 AI 员工、工具、知识、工单与任务、权限治理 ——  
全部收进一个桌面工作台。**数据默认留在本机，开源、可审计、可扩展。**

<br/>

[![Version](https://img.shields.io/badge/version-v0.4.0-8B7CFF?style=flat-square)](https://github.com/wu1w/tevarn/releases/tag/v0.4.0)
[![License](https://img.shields.io/badge/license-MIT-22D8EE?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%20first%20%7C%20Linux-1D2330?style=flat-square)](https://github.com/wu1w/tevarn/releases)
[![Local-first](https://img.shields.io/badge/mode-LOCAL--FIRST-6D5DF6?style=flat-square)](#)
[![Stars](https://img.shields.io/github/stars/wu1w/tevarn?style=flat-square&logo=github)](https://github.com/wu1w/tevarn/stargazers)

<br/>

`LICENSE MIT` · `VERSION v0.4.0` · `MODE LOCAL-FIRST` · `STACK RUST + NEXT.JS + FASTAPI + FLUTTER`

<br/>

[快速开始](#-快速开始) · [核心特性](#-核心特性) · [双端联动](#-双端联动) · [权限法院](#-权限法院) · [下载安装](#-下载安装) · [开发启动](#-开发启动) · [文档](#-文档)

</div>

---

## 这是什么

| 适合 | 不是 |
|------|------|
| 个人开发者 / 研究者 / 自动化爱好者 | 企业多租户 SaaS / 组织级 OA |
| 数据默认本机 SQLite · 默认只监听 `127.0.0.1` | 云端 SLA / 强制上云 |
| **Windows 优先**（Linux 可用） | 必须 Redis / Qdrant 才能跑 |

> **状态**：主线为 **`main`** · 当前稳定版 **[v0.4.0](https://github.com/wu1w/tevarn/releases/tag/v0.4.0)**（Tevarn 新主线首发）。  
> 历史 Releases **0.3.x（Takton）** 为旧桌面线；**0.5.x-alpha** 为预览迭代，现已收敛进 `main` / v0.4.0。

---

## 核心特性

> 一台机器，一整支 AI 团队 —— 从雇佣 AI 员工到权限治理，从用量账本到技能自生长。

| # | 能力 | 说明 |
|---|------|------|
| 01 | **AI 员工 Crew** | 在本机雇佣并调度 AI 员工，带预算、授权与记忆总线 |
| 02 | **统一 Run 模型** | chat / inbox / cron 共享运行模型，支持检查点恢复 |
| 03 | **权限法院** | 一条决策路径 · layer / rule / verdict · Kernel 页可视化 |
| 04 | **用量与缓存账本** | 真实 token / billable / prompt-cache，非 mock |
| 05 | **技能自动生成** | 新任务类型可自写工具 · 内置技能 + 可扩展 |
| 06 | **OS 级能力** | 文件 / 终端 / 浏览器 / SQLite，全部过权限模型 |
| 07 | **智能编排** | 简单问题单 Agent；复杂任务可选并行草稿扇出（默认关） |
| 08 | **双端联动** | Windows 工作站 + 手机端远程遥控 / 审批 |

---

## 双端联动

### 桌面是主场 · Windows 工作站

完整 AIOS 桌面工作台：AI 员工、Kernel 可视化、工单任务、用量账本，全在本机运行。

- Electron 桌面壳
- Rust Kernel Host + FastAPI
- SQLite · 默认 `127.0.0.1`
- **不强制** Redis / Qdrant

### 手机是遥控器 · 移动端

Flutter 安卓端（iOS / 鸿蒙推进中）：远程查看运行、审批权限、调度任务。

- 扫码配对 PC
- 运行监控与权限批准
- 同一套本地系统，口袋控制台

---

## 权限法院

**每一次操作，都要过堂。**

AI 能做事，更要守规矩。所有 OS 级能力收敛进 **一条决策路径**，三级治理可审计、可拦截、可回放：

1. **LAYER** · 层级判定 — request 进入 layer pipeline  
2. **RULE** · 规则匹配 — 与 policy rules 匹配  
3. **VERDICT** · 判决执行 — allow / deny，全程写入 Kernel  

Kernel 页可视化判决过程，不是黑盒。

---

## 快速开始

```bash
# 1. 克隆仓库（默认 main = 新主线）
git clone https://github.com/wu1w/tevarn.git
cd tevarn

# 2. 使用稳定标签（可选）
git checkout v0.4.0

# 3. 按下方「开发启动」或安装预编译包
```

默认本地运行，无需任何云服务。  
版本 **v0.4.0** · 分支 **`main`**。

---

## 下载安装

### 官网

| 线路 | 地址 |
|------|------|
| 国际 | https://tevarn.com |
| **大陆（推荐）** | **https://cdn.jsdmirror.com/gh/wu1w/tevarn@main/website/** |

> `tevarn.com` 经 Cloudflare，部分大陆网络会打不开。请用上表「大陆」镜像（公共 CDN，无需备案）。说明见 [docs/CHINA_ACCESS.md](docs/CHINA_ACCESS.md)。

### Windows / Android 安装包

| 类型 | 文件名 |
|------|--------|
| 安装版 Setup | `Tevarn-Setup-0.4.0-x64.exe` |
| 便携版 | `Tevarn-0.4.0-win-portable.exe` |
| 压缩包 | `Tevarn-0.4.0-win-x64.zip` |
| 手机 APK | `Tevarn-Mobile-0.4.0.apk` |

优先打开 [大陆官网下载区](https://cdn.jsdmirror.com/gh/wu1w/tevarn@main/website/#download)；或见 [Releases · v0.4.0](https://github.com/wu1w/tevarn/releases/tag/v0.4.0)。

本地构建产物：

```text
frontend/release/Tevarn-Setup-0.4.0-x64.exe
mobile/dist/Tevarn-Mobile-0.4.0.apk
```
### 从源码打桌面包

```bash
# 需 Node.js、Rust（kernel host）、Windows 打包环境
npm run ensure:vendor-host   # 或 cargo build -p tevarn-kernel-host --release
cd frontend
npm install
npm run dist:win
```

### 从源码打手机 APK

```bash
cd mobile/flutter_app
flutter pub get
flutter build apk --release
# 产物：build/app/outputs/flutter-apk/app-release.apk
```

---

## 开发启动

### 前置

- **Node.js 20+** · **Python 3.11+** · **Rust**（Kernel Host）
- Windows 推荐；Linux 可用

### 1. Kernel Host

```bash
cargo build -p tevarn-kernel-host --release
# 二进制：target/release/tevarn-kernel-host(.exe)
# 也可放入 vendor/tevarn-kernel-host/
```

### 2. 后端

```bash
cd backend
pip install -r requirements.txt
# Windows 安全入口（Selector 事件循环）
python -m backend.win_boot --host 127.0.0.1 --port 8090
# 或：python -m uvicorn backend.main:app --host 127.0.0.1 --port 8090
```

### 3. 前端

```bash
cd frontend
npm install
npm run dev
# http://127.0.0.1:3000
```

### 4. 桌面壳（可选）

```bash
# 仓库根目录
npm run electron:dev
```

环境变量以 **`TEVARN_*`** 为准（兼容旧 `TAKTON_*`）。数据目录优先 `~/.tevarn`（已有 `~/.takton` 时软迁移）。

---

## 架构一览

```text
┌─────────────────────────────────────────────────┐
│  Electron / Next.js 工作台 · Flutter 手机端      │
└───────────────────────┬─────────────────────────┘
                        │  HTTP / WS  127.0.0.1
┌───────────────────────▼─────────────────────────┐
│  FastAPI 后端 · Agent Loop · 工具 · 权限法院     │
└───────────────────────┬─────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────┐
│  tevarn-kernel-host (Rust) · 进程 / 预算 / 审计  │
└───────────────────────┬─────────────────────────┘
                        │
                   SQLite (本机)
```

| 层 | 技术 |
|----|------|
| 桌面 UI | Next.js 16 · Electron |
| 手机 UI | Flutter |
| 控制面 | FastAPI · Agent loop |
| Kernel | Rust `tevarn-kernel-host` |
| 存储默认 | SQLite · 本机路径 |

---

## 开源信息

| | |
|--|--|
| **协议** | MIT — 自由使用、修改与分发 |
| **版本** | **v0.4.0** · 新主线首个稳定版 |
| **本地优先** | 默认 SQLite · 只监听回环 · 不强制云组件 |

欢迎 Issue 与 PR。

---

## 文档

| 文档 | 说明 |
|------|------|
| [技术手册](docs/TECHNICAL_MANUAL.md) | 架构与运维细节 |
| [KERNEL_RUST](docs/KERNEL_RUST.md) | Kernel Host 构建与 ABI |
| [威胁模型](docs/THREAT_MODEL.md) | 安全边界 |
| [路线图](docs/ROADMAP.md) | 版本规划 |
| [官网落地页](website/index.html) | 冷白机身 · Light Console 产品页 |
| [大陆访问说明](docs/CHINA_ACCESS.md) | 国内镜像 / OSS / 灰云 VPS |

本地预览官网：

```bash
npx --yes serve website -p 5173
```

大陆镜像（push `main` 后约数分钟生效）：

```text
https://cdn.jsdmirror.com/gh/wu1w/tevarn@main/website/
```
---

## 从旧版升级

| 线 | 分支 / 版本 | 说明 |
|----|-------------|------|
| **当前主线** | **`main`** · **[v0.4.0](https://github.com/wu1w/tevarn/releases/tag/v0.4.0)** | Tevarn · Rust Kernel · 编制/工单 · 权限法院 · 双端 |
| **历史预览** | `feature/agent-kernel` / 0.5.x-alpha | 已并入 main，可作历史参考 |
| **旧桌面线** | Releases 0.3.x（Takton） | 较早桌面 Agent |

- 新用户 → clone **`main`** 或下载 **v0.4.0** 安装包  
- 品牌 **Tevarn**；环境变量 `TEVARN_*` 优先，仍兼容 `TAKTON_*`

---

<div align="center">

**Tevarn** — 个人本地开源 AIOS 工作站

[GitHub](https://github.com/wu1w/tevarn) · [Issues](https://github.com/wu1w/tevarn/issues) · [Releases](https://github.com/wu1w/tevarn/releases)

<br/>

<sub>MIT License · Local-first · Windows-first</sub>

</div>
