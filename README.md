# Takton

个人多机 Agent 工作台（桌面 / 浏览器一键装）。

## 小白怎么装（几乎不用想）

### Windows（推荐）

1. **安装包**（若有 Release）：双击 `Takton Setup.exe` → 打开桌面图标。  
   首次启动会自动补齐 Python 依赖（内嵌解释器时更快）。

2. **没有安装包时** — 打开 **PowerShell**，整行粘贴：

```powershell
irm https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.ps1 | iex
```

脚本会自动：选对的 Python（3.10–3.13）→ 下载代码 → 建独立环境 → 装依赖 → 自检 → 打开浏览器。

### Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.sh | bash
```

装完打开：`http://127.0.0.1:8090`

以后启动：

```bash
# Linux/mac
takton start

# Windows
%USERPROFILE%\.takton\bin\takton.cmd start
```

### 装之前只要有这些（没有会提示怎么下）

| 平台 | 需要 |
|------|------|
| Windows 源码一键 | Git + Python **3.11 或 3.12**（别用 3.14） |
| Linux/mac 源码一键 | git + curl + Python 3.10–3.13 |
| Windows 安装包 | 一般什么都不用装 |

## 开发者

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r backend/requirements-prod.txt
pip install -e .
takton start --port 8090
```

前端：`cd frontend && npm install && npm run dev`

## 说明

- 默认单用户本地登录，无需注册。
- 密钥写在 `~/.takton/.env`（Windows: `%USERPROFILE%\.takton\.env`），不要提交到 Git。
- 仓库内知识库种子为通用教程，不含私人主机名/内网地址。
