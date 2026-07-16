# 为什么不能「把 venv 直接打进 Git / 拷给用户」

## 结论

| 做法 | 是否可行 | 说明 |
|------|----------|------|
| 把整份 `.venv` 提交到 GitHub | ❌ | 体积巨大；`pyvenv.cfg` / 脚本里写死本机路径；Linux/Windows/mac 互不通用 |
| 用户从 A 电脑 zip 拷贝 venv 到 B | ❌ | 同上，几乎必挂 |
| **本机一键脚本生成隔离环境** | ✅ | 用户只跑一行命令；脚本自动下 Python + 依赖 |
| **Windows Setup.exe 内嵌 win-python + 已装好的 site-packages** | ✅ | 真正「下一步下一步」；由 `prepare-win-python` 在打包时灌依赖 |
| Release 附带 offline-wheels 离线包 | ✅ 可选 | `pip install --no-index -f wheels`；适合内网 |

## 我们采用的傻瓜方案

### 1. 源码一键（GitHub）

- Windows: `irm .../install.ps1 | iex`
- Linux/mac: `curl .../install.sh | bash`
- **没有合适 Python 时**：自动装 `uv` → 下载便携 **CPython 3.12** → 在 `%USERPROFILE%\.takton\venv`（或 `~/.takton/venv`）建环境 → 装 `requirements-prod` → 自检 → 启动  
- 用户感知 ≈「拉项目 + 自动配好环境」，而不是自己配 venv

### 2. 桌面安装包（exe）

- `npm run dist:win` 走 `prepare-win-python`：把 prod 依赖灌进 `win-python`
- 用户双击 Setup → 下一步 → 打开即可；缺包时 Electron 还会装到可写的 `userData/python-packages`

## 不要做的事

- 不要把 `.venv`、`win-python` 全量提交进仓库（已在 `.gitignore`）
- 不要把含私钥的 `.env`、真实内网 IP、设备名提交公开仓

## 内网离线（可选进阶）

在有网机器：

```bash
pip download -r backend/requirements-prod.txt -d offline-wheels
```

把 `offline-wheels` 拷到内网后：

```bash
pip install --no-index --find-links=offline-wheels -r backend/requirements-prod.txt
pip install --no-index --find-links=offline-wheels -e .
```
