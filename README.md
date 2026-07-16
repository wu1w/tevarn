# Takton

个人多机 Agent 工作台（对话调度本机 / 远程设备 · FastAPI + Next.js · 可选 Electron 桌面）。

仓库：https://github.com/wu1w/takton

> **平台支持：** Windows、Linux 为主要支持目标。**macOS 因无 Mac 测试机，脚本仅实验性提供，不保证可用。**

---

## 选哪种安装方式？

| 你是谁 | 推荐方式 | 你要做什么 |
|--------|----------|------------|
| 完全小白（Windows） | **① 安装包 Setup.exe** | 双击 → 下一步 → 打开 |
| 会打开 PowerShell | **② Windows 一行命令** | 复制粘贴一行 |
| Linux | **③ 一行 shell** | 复制粘贴一行 |
| macOS | **③ 一行 shell（实验）** | 复制粘贴一行；**未在真机验证，不保证可用** |
| 开发者 / 改代码 | **④ 源码开发安装** | venv + pip |
| 公司内网、不能上网装依赖 | **⑤ 离线 wheel 包** | 见文末 |

> **不要**把别人电脑上的 `.venv` 文件夹直接拷过来用（路径写死，必挂）。  
> 一键脚本会在**你自己的电脑**上自动生成等价环境，体验接近「自带 venv」。

---

## ① Windows 安装包（最傻瓜）

1. 打开 [Releases](https://github.com/wu1w/takton/releases)（若已发布）
2. 下载 **`Takton Setup x.y.z.exe`**
3. 双击安装，一路「下一步」
4. 从桌面打开 **Takton**

- 安装包内嵌 Python 与生产依赖（打包时由 `prepare-win-python` 灌入）
- 一般**不需要**再装系统 Python / Node
- 首次启动若缺包，会装到用户目录下的可写路径

> 若 Releases 里还没有安装包：用下面的 **②**，或让维护者执行 `cd frontend && npm run dist:win` 打出安装包。

---

## ② Windows 一行命令（源码自动装环境）

### 你需要事先有的

- 能上网  
- [Git for Windows](https://git-scm.com/download/win)（安装后**重新打开** PowerShell）  
- **Python 可选**：没有，或只有 3.14，脚本会尝试用 **uv 自动下载便携 Python 3.12**

### 安装

1. 按 `Win + X`，打开 **Windows PowerShell** 或 **终端**
2. **整行复制**粘贴回车：

```powershell
irm https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.ps1 | iex
```

### 脚本会自动做

1. 选择合适的 Python（3.10–3.13；否则 uv 装 3.12）  
2. 从 GitHub 克隆代码到 `%USERPROFILE%\.takton\src`  
3. 创建独立环境 `%USERPROFILE%\.takton\venv`  
4. 安装 `backend/requirements-prod.txt` + Takton  
5. 自检能否 `import`  
6. 写入本地密钥 `%USERPROFILE%\.takton\.env`  
7. 默认启动并尝试打开浏览器  

### 装好后怎么用

```text
浏览器打开:  http://127.0.0.1:8090
以后启动:    %USERPROFILE%\.takton\bin\takton.cmd start
只安装不启动（可选）:
  $env:TAKTON_NO_START = "1"
  irm https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.ps1 | iex
```

### 可选环境变量

| 变量 | 含义 | 默认 |
|------|------|------|
| `TAKTON_HOME` | 安装目录 | `%USERPROFILE%\.takton` |
| `TAKTON_PORT` | 端口 | `8090` |
| `TAKTON_NO_START` | `1` = 只装不启动 | `0` |
| `TAKTON_SOURCE` | 用本地已有源码目录 | （空则 git clone） |
| `TAKTON_REPO` / `TAKTON_REF` | 仓库与分支 | `wu1w/takton` · `main` |

---

## ③ Linux / macOS 一行命令

> ⚠️ **macOS 说明：** 当前维护环境没有 Mac 真机，安装脚本与运行路径**未做完整测试**，仅按通用 Unix 逻辑编写，**不保证能装上或稳定使用**。欢迎反馈 Issue；正式使用请优先 Windows / Linux。

### 你需要事先有的

- `curl`、`git`  
- 能访问 GitHub  
- Python 3.10–3.13 **或** 允许脚本用 uv 下载便携 3.12  

Debian/Ubuntu 若完全没有工具，可先：

```bash
sudo apt update
sudo apt install -y curl git
# 可选（有系统 Python 时更快）:
# sudo apt install -y python3.12 python3.12-venv
```

### 安装

```bash
curl -fsSL https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.sh | bash
```

### 装好后

```bash
# 浏览器
# http://127.0.0.1:8090

takton start          # 若已写入 PATH
# 或
~/.takton/bin/takton start

# 只安装不启动
TAKTON_NO_START=1 curl -fsSL https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.sh | bash
```

### 可选环境变量

与 Windows 相同含义：`TAKTON_HOME`（默认 `~/.takton`）、`TAKTON_PORT`、`TAKTON_NO_START`、`TAKTON_SOURCE`、`TAKTON_REPO`、`TAKTON_REF`。

---

## ④ 开发者：源码手动安装

适合改代码、跑测试、打安装包。

```bash
git clone https://github.com/wu1w/takton.git
cd takton

# 建议 Python 3.11 或 3.12（避开 3.14）
python -m venv .venv
# Windows:
#   .venv\Scripts\activate
# Linux/mac:
source .venv/bin/activate

pip install -U pip
pip install -r backend/requirements-prod.txt
pip install -e .

# 启动（API + 内置静态前端）
takton start --port 8090
# 浏览器 http://127.0.0.1:8090
```

### 前端热更新开发

```bash
# 终端 1：后端
takton start --dev --port 8090

# 终端 2：前端
cd frontend
npm install
npm run dev
```

### 打 Windows 桌面安装包

```powershell
cd frontend
npm install
npm run dist:win
# 产物: frontend/release/Takton Setup 0.1.0.exe
```

打包时会执行 `prepare:win-python`，把生产依赖灌进内嵌 Python，用户安装后尽量免配环境。

---

## ⑤ 内网 / 离线依赖（可选）

在有网机器下载 wheel：

```bash
pip download -r backend/requirements-prod.txt -d offline-wheels
```

拷到内网后：

```bash
pip install --no-index --find-links=offline-wheels -r backend/requirements-prod.txt
pip install --no-index --find-links=offline-wheels -e .
```

说明见 [docs/PACKAGING-VENV.md](./docs/PACKAGING-VENV.md)。

---

## 装好后怎么确认成功

1. 浏览器打开 `http://127.0.0.1:8090`（或你设的端口）能看到界面  
2. 健康检查：

```bash
curl http://127.0.0.1:8090/api/health
# 期望: {"status":"ok","service":"takton-backend"}
```

3. 默认单用户本地模式，一般无需注册；密钥在  
   - Windows: `%USERPROFILE%\.takton\.env`  
   - Linux/mac: `~/.takton/.env`  

**请勿**把 `.env`、API Key、真实内网地址提交到 Git。

---

## 常见问题

| 现象 | 处理 |
|------|------|
| 只有 Python 3.14，安装失败 | 用一键脚本（会拉 3.12），或再装 3.11/3.12 |
| `git` 不是内部或外部命令 | 安装 Git for Windows 后**重开**终端 |
| 克隆很慢 / 超时 | 检查代理或 GitHub 访问；可设 `TAKTON_SOURCE` 指向已下载的源码目录 |
| 端口被占用 | `$env:TAKTON_PORT=18090` 或 `TAKTON_PORT=18090` 再装/再启动 |
| 想重装 | 删掉 `~/.takton`（Win: `%USERPROFILE%\.takton`）后重新跑安装命令 |
| macOS 装不上 / 跑不起来 | **预期内可能失败**：我们没有 Mac 测试机，不保证可用；请用 Windows/Linux 或自助排查后提 Issue |
| 为什么不直接发 venv？ | 不能跨机使用；详见 [docs/PACKAGING-VENV.md](./docs/PACKAGING-VENV.md) |

---

## 文档索引

| 文档 | 内容 |
|------|------|
| [PACKAGING.md](./PACKAGING.md) | 打包与分发 |
| [docs/PACKAGING-VENV.md](./docs/PACKAGING-VENV.md) | 为何不提交 venv、如何一键生成环境 |
| [docs/DIALOG-CONFIG-SYSTEM.md](./docs/DIALOG-CONFIG-SYSTEM.md) | 对话配置 Takton |
| [docs/PRD-REMOTE-ARCHITECTURE.md](./docs/PRD-REMOTE-ARCHITECTURE.md) | 多机远程架构 |

---

## 许可证与说明

- 默认本地单用户使用；生产公网部署请自行加固鉴权与密钥。  
- 公开仓库内的知识库种子为**通用教程**，不含私人主机名 / 内网地址。  
