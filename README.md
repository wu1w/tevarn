# Takton

个人多机 Agent 工作台。

## 小白安装（尽量零脑）

### Windows

**A. 安装包（最傻瓜）**  
双击 `Takton Setup.exe` → 一直下一步 → 打开。依赖已在打包时灌进内嵌 Python。

**B. 一行命令（没有安装包时）**  
打开 PowerShell，粘贴：

```powershell
irm https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.ps1 | iex
```

- 没有 Python / 只有 3.14：**脚本会自动用 uv 下载便携 Python 3.12**
- 会自动：下代码 → 建本机环境 → 装依赖 → 自检 → 打开浏览器  
- 需要：能上网 + 已装 [Git for Windows](https://git-scm.com/download/win)

### Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.sh | bash
```

同样会在缺 Python 时尝试用 uv 自举 3.12。

装好后地址：`http://127.0.0.1:8090`  
以后启动：`takton start`（Windows：`%USERPROFILE%\.takton\bin\takton.cmd start`）

### 为什么不直接发一个 venv 文件夹？

venv **不能跨机器拷贝**（路径写死、系统不同）。  
我们做的是：**一键在你电脑上生成一份等价的隔离环境**，体验接近「打包好的环境」。  
详见 [docs/PACKAGING-VENV.md](./docs/PACKAGING-VENV.md)。

## 开发者

```bash
python -m venv .venv && source .venv/bin/activate  # Win: .venv\Scripts\activate
pip install -r backend/requirements-prod.txt && pip install -e .
takton start --port 8090
```

## 说明

- 密钥在 `~/.takton/.env`，勿提交 Git  
- 公开仓已去掉私人主机名 / 内网示例地址  
