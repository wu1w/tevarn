# Takton

个人多机 Agent 工作台（桌面客户端 · 对话调度本机 / 远程设备）。

仓库：https://github.com/wu1w/takton

## 安装（桌面客户端）

### Windows（推荐）

PowerShell 一行安装并打开 **Takton** 客户端：

```powershell
irm https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.ps1 | iex
```

或手动下载安装包：

- [Takton-Setup-0.1.0.exe](https://github.com/wu1w/takton/releases/download/v0.1.0/Takton-Setup-0.1.0.exe)
- 更多版本见 [Releases](https://github.com/wu1w/takton/releases)

### Linux

```bash
curl -fsSL https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.sh | tr -d '\015' | bash
```

或手动下载：

- [Takton-0.1.0.AppImage](https://github.com/wu1w/takton/releases/download/v0.1.0/Takton-0.1.0.AppImage)

> **macOS**：无真机测试，不保证可用。

---

## 说明

| 方式 | 结果 |
|------|------|
| 一行脚本 / Setup.exe / AppImage | **桌面客户端**（内嵌后端） |
| 开发改代码 | 见下方「开发者」 |

一键脚本会从 GitHub Releases **下载客户端安装包并安装**，不是单独搭一套仅浏览器环境。

需要：

- Windows：能上网；建议已装 [Git](https://git-scm.com/download/win)（一般不必）
- Linux：`curl` 或 `wget`

可选环境变量：

| 变量 | 含义 |
|------|------|
| `TAKTON_RELEASE_TAG` | 默认 `v0.1.0` |
| `TAKTON_NO_START=1` | 只装不自动启动 |
| `TAKTON_HOME` | Linux AppImage 安装目录（默认 `~/.local/share/takton`） |

---

## 开发者

源码开发、改引擎、跑测试：

```bash
git clone https://github.com/wu1w/takton.git
cd takton
# 后端
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r backend/requirements.txt  # 或 requirements-prod.txt
# 前端
cd frontend && npm install && npm run dev
```

打桌面安装包：

```bash
cd frontend
npm run dist:win    # → release/Takton Setup *.exe
npm run dist:linux  # → AppImage / deb
```

---

## 许可证与其它

详见仓库内文档与源码。问题与需求请开 Issue。
