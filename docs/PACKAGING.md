# Takton 打包说明（0.1.0+）

## 关键修复（相对旧 release）

1. **密钥持久化**：`jwt` / `api_key` / `encryption_salt` 写入 `userData/secrets.json`，跨重启稳定。
2. **不打包开发 `.env`**：安装包不再内嵌内网 LLM 地址与弱密钥。
3. **平台资源分离**：
   - Windows：只打 `win-python`（且应预装依赖）
   - Linux：只打本机 `.venv`
   - macOS：**不要**再打 Linux 的 `.venv`；需在 macOS 真机构建或单独提供 mac 运行时
4. **可写数据目录**：DB / uploads / workspace 全部进 `userData/data/`。
5. **依赖安装**：缺依赖时安装到 `userData/python-packages`（可写），不写 Program Files。

## Windows 打包

```powershell
cd frontend
npm install
npm run prepare:win-python   # 把 requirements 装进 win-python
npm run dist:win
```

产物：`frontend/release/Takton Setup 0.1.0.exe`

## Linux 打包（在 Linux 主机）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cd frontend && npm ci
npm run dist:linux
```

## macOS 打包（必须在 macOS 上）

当前配置**不会**把 Linux venv 打进 Mac 包。请在目标架构的 Mac 上：

1. 准备 macOS Python 运行时（或 `.venv`）
2. 在 `package.json` 的 `mac.extraResources` 中指向该运行时
3. `npm run dist:mac`

交叉从 Linux 打 Mac 包且塞入 Linux `.venv` 会导致无法启动。

## 本地后端冒烟

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-dev-e2e.ps1
```
