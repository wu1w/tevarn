# Takton 安装指南（Phase 5）

> 产品版本（feature 分支）：**0.4.10-alpha**  
> 目标：全新机器 **约 10 分钟** 从零到可对话。  
> 默认零外部依赖：SQLite · 无 Redis · 无 Qdrant（见 `docs/ZERO_DEPS.md`）。

---

## 路径 R：Release 桌面客户端（终端用户）

从 GitHub Release 下载安装包（需已发布对应 tag 资产）。

### Windows

```powershell
iex ((irm https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.ps1) -replace '^\uFEFF','')
```

或手动下载 `Takton-Setup-*.exe`（NSIS）。

### Linux

```bash
curl -fsSL https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.sh | tr -d '\r' | bash
```

**成功标准**：打开客户端 → 单用户/登录 → 发送一条 chat 有回复。

> `install.ps1` / `install.sh` 只负责 **下载 Release**，不编译源码。

---

## 路径 S：源码开发者 / dogfood（推荐 feature 分支）

### 前置

- Python 3.11+（推荐 3.12/3.13）
- Node.js 20+（前端）
- Git
- 至少一个 LLM API Key（OpenAI 兼容 / 本地网关均可）

### Windows（PowerShell）

```powershell
git clone https://github.com/wu1w/takton.git
cd takton
git checkout feature/agent-kernel   # 或当前 feature 分支

# 一键 bootstrap（venv + deps + frontend npm）
powershell -ExecutionPolicy Bypass -File scripts/bootstrap_dev.ps1

# 配置 LLM Key 等到 .env（start.py 可自动生成 JWT）
.\.venv\Scripts\python.exe start.py
# 浏览器打开提示地址（前端通常 :3000，后端默认 :8090）
```

手动等价：`py -3 -m venv .venv` → `pip install -e ".[dev]"` → `frontend/npm install`。

### 成功标准

1. 后端健康（日志无 FATAL）  
2. 前端可打开  
3. 发出第一条用户消息并收到模型回复  

### 计时记录

实测请写入 `reports/PHASE5_INSTALL_TIMING.md`（机器 / 分支 / 耗时 / 卡点）。

---

## 可选加速（非默认）

| 组件 | 默认 | 开启后 |
|------|------|--------|
| Redis | 关 | 多 worker Kernel 热共享 |
| Qdrant + Embedding | 关（空 URL） | 向量 RAG |
| Electron | 开发用 `start.py` | `npm run dist:win` 打 NSIS |

---

## 故障排查

| 现象 | 处理 |
|------|------|
| `import sqlalchemy` 失败 | 确认用的是项目 `.venv`，不是 PATH 其它 venv |
| 端口占用 | 改 `PORT` / 关掉旧 uvicorn |
| JWT 弱密钥拒绝启动 | 删除错误的环境变量，让其自动生成 |
| 非 loopback + single_user | 安全自检 fail：改回 127.0.0.1 或关 single_user |

---

## 版本

权威：`backend/VERSION` → 当前 **0.4.10-alpha**。  
同步：`python scripts/sync_version.py --check`
