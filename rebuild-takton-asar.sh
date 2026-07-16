#!/usr/bin/env bash
# Takton asar 重新打包脚本
# 用途：当 Takton 前端代码修改后，重新构建并打包 app.asar
# 运行环境：Windows + MSYS/git-bash
# 要求：先关闭 Takton.exe（脚本会强制结束）

set -euo pipefail

# ---- 配置路径 ----
PROJECT_ROOT="/e/项目/taktonl-0.1.0"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
RESOURCES_DIR="$FRONTEND_DIR/release/win-unpacked/resources"
APP_ASAR="$RESOURCES_DIR/app.asar"
APP_ASAR_UNPACKED="$RESOURCES_DIR/app.asar.unpacked"
BACKUP_DIR="$RESOURCES_DIR/_asar_backups"
WORK_DIR="$FRONTEND_DIR/release/_asar_rebuild"
EXTRACT_DIR="$WORK_DIR/_extracted_current"

# 在 frontend 目录下使用的相对路径（npx asar 在 Windows 上无法解析 MSYS 绝对路径）
APP_ASAR_REL="release/win-unpacked/resources/app.asar"
EXTRACT_REL="release/_asar_rebuild/_extracted_current"

# ---- 日志输出 ----
log_info()  { echo "[INFO]  $*"; }
log_warn()  { echo "[WARN]  $*"; }
log_error() { echo "[ERROR] $*"; }
log_ok()    { echo "[OK]    $*"; }

# ---- 前置检查 ----
if ! command -v npx >/dev/null 2>&1; then
  log_error "npx 命令未找到，请确认 Node.js 已安装并在 PATH 中"
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  log_error "node 命令未找到"
  exit 1
fi

# ---- 步骤 1：关闭 Takton 进程 ----
log_info "步骤 1/10：关闭 Takton 进程..."
if taskkill.exe /F /IM Takton.exe 2>/dev/null || true; then
  sleep 2
  log_ok "Takton 进程已关闭"
else
  log_warn "未找到 Takton 进程，或已关闭"
fi

# ---- 步骤 2：备份当前 asar ----
log_info "步骤 2/10：备份当前 asar..."
mkdir -p "$BACKUP_DIR"
BACKUP_NAME="app.asar.bak-$(date +%s)"
if [ -f "$APP_ASAR" ]; then
  cp -a "$APP_ASAR" "$BACKUP_DIR/$BACKUP_NAME"
  log_ok "已备份到 $BACKUP_DIR/$BACKUP_NAME ($(du -sh "$BACKUP_DIR/$BACKUP_NAME" | awk '{print $1}'))"
else
  log_warn "$APP_ASAR 不存在，跳过备份"
fi

# ---- 步骤 3：清理 .next 缓存 ----
log_info "步骤 3/10：清理 .next 缓存..."
rm -rf "$FRONTEND_DIR/.next"
log_ok ".next 缓存已清理"

# ---- 步骤 4：前端静态构建 ----
log_info "步骤 4/10：前端静态构建（NEXT_EXPORT=1 npm run build）..."
(
  cd "$FRONTEND_DIR"
  NEXT_EXPORT=1 npm run build
)

# ---- 步骤 5：验证 dist 存在 ----
log_info "步骤 5/10：验证 dist/index.html 存在..."
if [ ! -f "$FRONTEND_DIR/dist/index.html" ]; then
  log_error "dist/index.html 不存在！构建失败或不是静态导出"
  exit 1
fi
log_ok "dist/index.html 存在"

# ---- 步骤 6：准备 asar 工作目录 ----
log_info "步骤 6/10：准备 asar 工作目录..."
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"

# 提取当前 asar 的内容（保留 node_modules/electron 等运行时依赖）
if [ -f "$APP_ASAR" ]; then
  (
    cd "$FRONTEND_DIR"
    npx asar extract "$APP_ASAR_REL" "$EXTRACT_REL"
  )
  log_ok "已提取当前 asar 到 $EXTRACT_DIR"
else
  log_error "当前 asar 不存在，无法提取基础结构"
  exit 1
fi

# 复制新的 dist 进去
rm -rf "$EXTRACT_DIR/dist"
cp -a "$FRONTEND_DIR/dist" "$EXTRACT_DIR/dist"
log_ok "已复制新的 dist/ 到工作目录"

# 复制更新的 electron 主进程/预加载脚本
if [ -d "$FRONTEND_DIR/electron" ]; then
  rm -rf "$EXTRACT_DIR/electron"
  cp -a "$FRONTEND_DIR/electron" "$EXTRACT_DIR/electron"
  log_ok "已复制 electron/ 到工作目录"
fi

# 复制 package.json（如果版本号或脚本有变化）
if [ -f "$FRONTEND_DIR/package.json" ]; then
  cp -a "$FRONTEND_DIR/package.json" "$EXTRACT_DIR/package.json"
  log_ok "已复制 package.json"
fi

# ---- 步骤 7：打包 asar（关键：--unpack 保留 node_modules）----
log_info "步骤 7/10：打包 asar..."
rm -f "$APP_ASAR"
rm -rf "$APP_ASAR_UNPACKED"

# 使用 --unpack 把 node_modules 留在外面，避免 asar 包含巨大文件
(
  cd "$FRONTEND_DIR"
  npx asar pack "$EXTRACT_REL" "$APP_ASAR_REL" --unpack "{node_modules/**/*}"
)

log_ok "asar 打包完成，大小：$(du -sh "$APP_ASAR" | awk '{print $1}')"

# ---- 步骤 8：验证 asar 内容 ----
log_info "步骤 8/10：验证 asar 内容..."

# 8.1 检查 dist/index.html
if (
  cd "$FRONTEND_DIR"
  npx asar list "$APP_ASAR_REL" | grep -q '^\\dist\\index.html$'
); then
  log_ok "dist/index.html 存在于 asar 中"
else
  log_error "dist/index.html 不在 asar 中！"
  exit 1
fi

# 8.2 检查 node_modules 是否在外部（unpacked）
# 注意：不同 asar 版本对 --unpack 语法支持不同，node_modules 在 asar 内也可接受
if [ -d "$APP_ASAR_UNPACKED/node_modules" ]; then
  log_ok "node_modules 已正确解包到 app.asar.unpacked/"
else
  NODE_COUNT=$(
    cd "$FRONTEND_DIR"
    npx asar list "$APP_ASAR_REL" 2>/dev/null | grep -c '\\node_modules\\' || true
  )
  if [ "$NODE_COUNT" -gt 0 ]; then
    log_ok "node_modules 已包含在 asar 中（共 $NODE_COUNT 个文件）"
  else
    log_warn "未检测到 node_modules"
  fi
fi

# 8.3 检查关键包是否存在
if (
  cd "$FRONTEND_DIR"
  npx asar list "$APP_ASAR_REL" 2>/dev/null | grep -q 'electron-updater'
) || [ -d "$APP_ASAR_UNPACKED/node_modules/electron-updater" ]; then
  log_ok "electron-updater 可用"
else
  log_error "electron-updater 缺失！"
  exit 1
fi

# 8.4 检查 asar 大小是否合理（正常 ~300-500MB，若 >1GB 则异常）
ASAR_SIZE_KB=$(du -k "$APP_ASAR" | awk '{print $1}')
ASAR_SIZE_MB=$((ASAR_SIZE_KB / 1024))
if [ "$ASAR_SIZE_MB" -gt 1200 ]; then
  log_error "asar 过大 (${ASAR_SIZE_MB}MB)，可能 node_modules 被包含进去了"
  exit 1
fi
log_ok "asar 大小合理：${ASAR_SIZE_MB}MB"

# 8.5 检查主入口文件
if (
  cd "$FRONTEND_DIR"
  npx asar list "$APP_ASAR_REL" 2>/dev/null | grep -q '^\\electron\\dist\\main.js$'
) || (
  cd "$FRONTEND_DIR"
  npx asar list "$APP_ASAR_REL" 2>/dev/null | grep -q '^\\electron\\main.js$'
); then
  log_ok "electron 主入口存在"
else
  log_error "electron 主入口不存在！"
  exit 1
fi

# ---- 步骤 9：启动 Takton.exe 验证（可选） ----
log_info "步骤 9/10：启动 Takton.exe 验证（可选）..."
TAKTON_EXE="$FRONTEND_DIR/release/win-unpacked/Takton.exe"
if [ -f "$TAKTON_EXE" ]; then
  log_ok "Takton.exe 已找到：$TAKTON_EXE"
  log_info "请手动启动 Takton.exe 验证窗口和后端是否正常工作"
else
  log_warn "未找到 Takton.exe：$TAKTON_EXE"
fi

# ---- 步骤 10：最终报告 ----
log_info "步骤 10/10：完成报告"
echo ""
echo "=========================================="
echo "  Takton asar 重新打包完成"
echo "=========================================="
echo "  asar 文件：$APP_ASAR"
echo "  asar 大小：$(du -sh "$APP_ASAR" | awk '{print $1}')"
echo "  备份目录：$BACKUP_DIR"
echo "  工作目录：$WORK_DIR"
echo "  dist 文件数：$(find "$FRONTEND_DIR/dist" -type f | wc -l)"
echo "=========================================="
echo ""
echo "如果 Takton 启动后黑屏，请检查："
echo "  1. 后端是否启动（端口 8000/8001）"
echo "  2. 控制台是否有 Frontend static dir not found 错误"
echo "  3. asar 中是否包含 dist/index.html"
echo ""
exit 0
