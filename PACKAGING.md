# Takton 打包与分发说明（0.1.0+ 瘦身版）

## 用户怎么装

### Windows（无脑安装包）

1. 双击 `Takton Setup x.y.z.exe`
2. 一键安装（NSIS oneClick）→ 桌面快捷方式
3. 打开即可用（内嵌 Python + 后端 + 静态前端）

构建：

```powershell
cd frontend
npm install
npm run prepare:win-python   # 装 prod 依赖进 win-python 并 prune
npm run dist:win
```

产物：`frontend/release/Takton Setup 0.1.0.exe`

### Linux / macOS（一行安装）

```bash
curl -fsSL https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.sh | bash
```

本地源码调试安装：

```bash
bash scripts/install.sh
# 或
TAKTON_SOURCE=/path/to/takton bash scripts/install.sh
```

安装后：

```bash
takton start          # http://127.0.0.1:8090
takton build          # 需要 Node 时重建前端静态资源
takton version
```

环境变量见 `scripts/install.sh` 头部注释（`TAKTON_HOME` / `TAKTON_PORT` / `TAKTON_NO_START`）。

---

## 瘦身要点（相对旧 release）

1. **Electron 生产 dependencies 仅 `electron-updater`**  
   Next/React/Mermaid 等全部在 `devDependencies`，**不再打进 app.asar 的 node_modules**（此前 ~470MB 垃圾）。
2. **`requirements-prod.txt`**：桌面包不带 pytest / asyncpg / asgi-lifespan。
3. **`prepare-win-python`**：装完后 prune tests / pytest / `.chm` / `__pycache__`。
4. **NSIS `oneClick: true`**：无向导目录选择，装完可直接运行。
5. **`electronLanguages`: en-US + zh-CN**，减小 locales。
6. **单体静态挂载**：`backend/static_frontend.py` — `takton start` 单进程侍 API + UI。

## 密钥与数据目录

- 桌面：`jwt` / `api_key` / `encryption_salt` → `userData/secrets.json`
- DB / uploads / workspace → `userData/data/`
- 缺依赖时装到 `userData/python-packages`（可写）

## 平台资源

| 平台 | 运行时 |
|------|--------|
| Windows | `win-python` 预装 prod 依赖 |
| Linux Electron | 本机构建的 `.venv`（可选 AppImage 路径） |
| Linux/mac CLI | `install.sh` 创建 `~/.takton/venv` |

## 开发

```bash
# 后端
pip install -r backend/requirements-dev.txt
pip install -e .
takton start --dev --port 8090

# 前端
cd frontend && npm run dev
```

## 校验瘦身

打包后检查：

```bash
# asar 内不应再有 next/mermaid 整树
npx asar list frontend/release/win-unpacked/resources/app.asar | grep node_modules | wc -l
# 期望：仅 electron-updater 及其少量依赖，数量级 << 1000

du -sh frontend/release/win-unpacked
du -sh frontend/release/Takton\ Setup*.exe
```
