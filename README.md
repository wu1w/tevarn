# Takton

个人专属 AI Agent 终端（Electron 桌面 + FastAPI 后端 + Next.js 前端）。

## 小白用户怎么装

### Windows（推荐一键）

**方式 A — 安装包（若已发布 Release）**  
下载并双击 **`Takton Setup x.y.z.exe`** → 一键安装 → 桌面打开。

**方式 B — 一行 PowerShell（从 GitHub 拉源码）**

```powershell
irm https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.ps1 | iex
```

装完默认：`http://127.0.0.1:8090`  
命令：`%USERPROFILE%\.takton\bin\takton.cmd start`

### Linux / macOS（一行）

```bash
curl -fsSL https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.sh | bash
```

装完后浏览器打开 `http://127.0.0.1:8090`（默认）。

```bash
takton start
takton build    # 需要 Node 时重建前端
takton version
```

更多打包/瘦身说明见 [PACKAGING.md](./PACKAGING.md)。

## 开发者

```bash
# 后端
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r backend/requirements-prod.txt
pip install -e .
takton start --dev --port 8090

# 前端
cd frontend && npm install && npm run dev
```

## 打包（Windows 桌面）

```powershell
cd frontend
npm install
npm run dist:win
# → frontend/release/Takton Setup 0.1.0.exe
```

## 说明

- 默认本地用户：`admin@takton.dev`（单用户模式 auto-login）
- 请勿把 API Key / Token 提交进仓库
- 知识库种子为通用新手手册，不含个人隐私内容
