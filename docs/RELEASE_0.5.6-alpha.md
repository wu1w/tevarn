# Tevarn 0.5.6-alpha · 2026-08-06

**分支**：`feature/agent-kernel`  
**版本权威**：`backend/VERSION` · `frontend/lib/appVersion.ts` · root/`frontend` `package.json`

## 本版要点

### Kernel Host（Windows 安装包关键修复）
- **打包布局下可找到 host 二进制**：`resources/tevarn-kernel-host/*`（Electron `extraResources`）不再只搜 `vendor/` / `target/`
- **启动 cwd 修复**：打包态不再用 asar 假路径 `resources/app` 作为 spawn cwd（Windows 上会导致 Host 直接起不来）
- **重启 Host 可用**：Electron 将 `TEVARN_KERNEL_HOST_BIN` / `TEVARN_RESOURCES_PATH` 注入后端，UI「重启 Host」taskkill 后可重新拉起

### 打包密钥卫生（勿把开发机 API/OAuth 打进发行包）
- Electron 后端环境 **剥离** 本机 `OPENAI_API_KEY` / `TEVARN_LLM_API_KEY` / OAuth token 等 shell 密钥
- 打包态设 `TEVARN_PACKAGED=1`，**不再加载 cwd `.env`**
- 新增 `scripts/pack-sanitize-env.mjs`：打包前清环境；发现 `.env` / `*.db` / secrets 则拒绝打包
- `extraResources` 过滤与 `build-windows-desktop.sh` 同步加固

## 升级注意
- 请用本版 **重新打包** 的 Windows 安装包；仅替换 asar 不够（Host 路径与 env 在主进程）
- 若 0.5.5 包曾带出开发机 Key/OAuth：**轮换密钥**；本机若与安装版共用 `%APPDATA%\\tevarn`，旧 DB 配置仍会显示，属 userData 而非安装包
- 打 Windows 包前务必：
  ```powershell
  .\scripts\build-kernel-host.ps1 -Release
  node scripts\pack-sanitize-env.mjs
  npm run dist:win
  ```

## 关联文件
- `backend/kernel_rust/client.py` · `electron/main.ts`
- `backend/core/config.py` · `scripts/pack-sanitize-env.mjs`
- `package.json` / `frontend/package.json` · `scripts/build-windows-desktop.sh`
