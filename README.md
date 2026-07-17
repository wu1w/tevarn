# Takton

个人专属 AI Agent 工作台 —— 桌面客户端，支持对话调度、任务自动化、知识库管理与工作流编排。

![Version](https://img.shields.io/badge/version-0.1.2-blue)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

## ✨ 核心功能

| 模块 | 说明 |
|------|------|
| **💬 智能对话** | 多会话管理，支持上下文压缩、目标追踪、断点续传 |
| **⚡ 任务系统** | 定时任务（Cron）、Webhook 触发、工作流编排 |
| **📚 知识库** | RAG 检索增强，支持文档上传、向量化存储（Qdrant） |
| **🔧 工具集成** | MCP 协议支持，可扩展自定义工具 |
| **🤖 多 Agent** | 子 Agent 集群、Agent 画像配置、技能系统 |
| **📱 多通道** | 支持 Web、API、Webhook 等多种接入方式 |

## 🚀 快速开始

### 桌面客户端（推荐）

#### Windows

```powershell
# PowerShell 一行安装
iex ((irm https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.ps1) -replace '^\uFEFF','')
```

或手动下载：[Takton-Setup-0.1.2.exe](https://github.com/wu1w/takton/releases/download/v0.1.2/Takton-Setup-0.1.2.exe)

#### Linux

```bash
curl -fsSL https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.sh | tr -d '\015' | bash
```

或手动下载：
- [Takton-0.1.2.AppImage](https://github.com/wu1w/takton/releases/download/v0.1.2/Takton-0.1.2.AppImage)
- [takton_0.1.2_amd64.deb](https://github.com/wu1w/takton/releases/download/v0.1.2/takton_0.1.2_amd64.deb)

> **macOS**：暂无真机测试，可从源码构建。

### 源码运行

```bash
# 克隆仓库
git clone https://github.com/wu1w/takton.git
cd takton

# 一键启动（自动检测 Python，启动前后端）
python start.py

# 或分别启动：
# 后端
pip install -r backend/requirements.txt
python backend/main.py

# 前端
cd frontend && npm install && npm run dev
```

访问 http://localhost:3000

## 📖 文档

- [技术手册](docs/TECHNICAL_MANUAL.md) — 架构、API、数据库设计
- [AGENTS.md](AGENTS.md) — AI 编程助手配置指南

## 🛠️ 技术栈

| 层级 | 技术 |
|------|------|
| **前端** | Next.js 16, React 19, Tailwind CSS 4, Electron |
| **后端** | FastAPI, SQLAlchemy 2.0, SQLite/PostgreSQL, Qdrant |
| **AI/LLM** | OpenAI-compatible API, MCP Protocol, RAG |
| **部署** | Electron Builder, Docker (可选) |

## 📦 构建安装包

```bash
cd frontend

# Windows
npm run dist:win    # → release/Takton Setup *.exe

# Linux
npm run dist:linux  # → AppImage / deb

# macOS（未测试）
npm run dist:mac
```

## 🤝 贡献

欢迎提交 Issue 和 Pull Request。

## 📄 许可证

MIT License — 详见 [LICENSE](LICENSE)

---

**Takton** — 让 AI 成为你的专属工作伙伴 🎯
