#!/usr/bin/env bash
# Takton one-line installer (Linux / macOS)
#   curl -fsSL https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.sh | bash
#
# 没有系统 Python 3.10–3.13 时，会自动安装 uv 并用它下载便携 CPython 3.12。

set -euo pipefail

TAKTON_HOME="${TAKTON_HOME:-$HOME/.takton}"
TAKTON_REPO="${TAKTON_REPO:-https://github.com/wu1w/takton.git}"
TAKTON_REF="${TAKTON_REF:-main}"
TAKTON_PORT="${TAKTON_PORT:-8090}"
TAKTON_NO_START="${TAKTON_NO_START:-0}"
VENV="$TAKTON_HOME/venv"
SRC="$TAKTON_HOME/src"
TOOLS="$TAKTON_HOME/tools"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '[takton] %s\n' "$*"; }
ok()   { printf '[takton] ✓ %s\n' "$*"; }
err()  { printf '[takton] ERROR: %s\n' "$*" >&2; }

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    err "缺少: $1"
    exit 1
  fi
}

pick_python() {
  for c in python3.12 python3.11 python3.13 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      ver=$("$c" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || true)
      major=${ver%%.*}; minor=${ver#*.}
      if [ "${major:-0}" -eq 3 ] && [ "${minor:-0}" -ge 10 ] && [ "${minor:-0}" -le 13 ]; then
        echo "$c"
        return 0
      fi
    fi
  done
  return 1
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return 0
  fi
  mkdir -p "$TOOLS"
  if [ -x "$TOOLS/uv" ]; then
    echo "$TOOLS/uv"
    return 0
  fi
  info "安装 uv（用于自动下载便携 Python）..."
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$TOOLS" sh
  if [ -x "$TOOLS/uv" ]; then
    echo "$TOOLS/uv"
    return 0
  fi
  if [ -x "$HOME/.local/bin/uv" ]; then
    echo "$HOME/.local/bin/uv"
    return 0
  fi
  err "uv 安装失败"
  exit 1
}

ensure_python() {
  if PY="$(pick_python)"; then
    ok "系统 Python: $PY ($($PY --version 2>&1))"
    echo "$PY"
    return 0
  fi
  UV="$(ensure_uv)"
  info "用 uv 安装便携 Python 3.12..."
  "$UV" python install 3.12
  PY="$("$UV" python find 3.12)"
  ok "便携 Python: $PY"
  echo "$PY"
}

bold "Takton 一键安装 — 尽量零配置"
need_cmd curl
need_cmd git

PY="$(ensure_python)"
mkdir -p "$TAKTON_HOME/data/uploads" "$TAKTON_HOME/data/workspace"

if [ -n "${TAKTON_SOURCE:-}" ]; then
  SRC="$(cd "$TAKTON_SOURCE" && pwd)"
  info "本地源码: $SRC"
elif [ -f "$(pwd)/backend/main.py" ] && [ -f "$(pwd)/pyproject.toml" ]; then
  SRC="$(pwd)"
  info "当前目录: $SRC"
else
  if [ -d "$SRC/.git" ]; then
    info "更新源码..."
    git -C "$SRC" fetch --depth 1 origin "$TAKTON_REF"
    git -C "$SRC" checkout -q FETCH_HEAD
  else
    info "从 GitHub 克隆..."
    rm -rf "$SRC"
    git clone --depth 1 --branch "$TAKTON_REF" "$TAKTON_REPO" "$SRC" \
      || git clone --depth 1 "$TAKTON_REPO" "$SRC"
  fi
  ok "源码就绪"
fi

[ -f "$SRC/backend/main.py" ] || { err "缺少 backend/main.py"; exit 1; }

info "创建本机独立环境 $VENV ..."
rm -rf "$VENV"
if command -v uv >/dev/null 2>&1 || [ -x "$TOOLS/uv" ]; then
  UV="$(command -v uv 2>/dev/null || echo "$TOOLS/uv")"
  "$UV" venv "$VENV" --python "$PY" --clear
else
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
. "$VENV/bin/activate"
export PYTHONPATH= PYTHONHOME= PYTHONNOUSERSITE=1

info "安装依赖..."
if command -v uv >/dev/null 2>&1 || [ -x "$TOOLS/uv" ]; then
  UV="$(command -v uv 2>/dev/null || echo "$TOOLS/uv")"
  if [ -f "$SRC/backend/requirements-prod.txt" ]; then
    "$UV" pip install -r "$SRC/backend/requirements-prod.txt" --python "$VENV/bin/python"
  else
    "$UV" pip install -r "$SRC/backend/requirements.txt" --python "$VENV/bin/python"
  fi
  "$UV" pip install -e "$SRC" --python "$VENV/bin/python"
else
  python -m pip install -U pip setuptools wheel -q
  if [ -f "$SRC/backend/requirements-prod.txt" ]; then
    pip install -r "$SRC/backend/requirements-prod.txt" -q
  else
    pip install -r "$SRC/backend/requirements.txt" -q
  fi
  pip install -e "$SRC" -q
fi
info "自检关键模块..."
export TAKTON_JWT_SECRET="install-selfcheck-$(python -c 'import secrets; print(secrets.token_hex(16))')"
export TAKTON_API_KEY="install-selfcheck-$(python -c 'import secrets; print(secrets.token_hex(16))')"
export TAKTON_SETTINGS_ENCRYPTION_SALT="$(python -c 'import secrets; print(secrets.token_hex(8))')"
export TAKTON_SINGLE_USER_MODE=true
python -c "import fastapi, uvicorn, sqlalchemy, aiosqlite, httpx, jose, backend.main; print('import_ok')"
ok "运行环境就绪"

if [ ! -f "$SRC/backend/static/index.html" ]; then
  info "未找到预构建前端；有 npm 时可: takton build"
else
  ok "前端静态资源已内置"
fi

ENV_FILE="$TAKTON_HOME/.env"
if [ ! -f "$ENV_FILE" ]; then
  JWT=$(python -c 'import secrets; print(secrets.token_hex(32))')
  API=$(python -c 'import secrets; print(secrets.token_hex(32))')
  SALT=$(python -c 'import secrets; print(secrets.token_hex(16))')
  cat >"$ENV_FILE" <<EOF
# Auto-generated — do not commit
TAKTON_JWT_SECRET=$JWT
TAKTON_API_KEY=$API
TAKTON_SETTINGS_ENCRYPTION_SALT=$SALT
TAKTON_DB_URL=sqlite+aiosqlite:///$TAKTON_HOME/data/takton.db
TAKTON_APP_HOST=127.0.0.1
TAKTON_APP_PORT=$TAKTON_PORT
TAKTON_SINGLE_USER_MODE=true
TAKTON_UPLOADS_DIR=$TAKTON_HOME/data/uploads
TAKTON_FILE_BROWSER_ROOT=$TAKTON_HOME/data/workspace
TAKTON_LOG_LEVEL=info
EOF
  ok "配置: $ENV_FILE"
fi

BIN_DIR="$TAKTON_HOME/bin"
mkdir -p "$BIN_DIR"
cat >"$BIN_DIR/takton" <<EOF
#!/usr/bin/env bash
set -euo pipefail
source "$VENV/bin/activate"
export PYTHONPATH= PYTHONHOME= PYTHONNOUSERSITE=1
set -a
source "$ENV_FILE"
set +a
export PYTHONPATH="$SRC\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$VENV/bin/python" -m backend.cli "\$@"
EOF
chmod +x "$BIN_DIR/takton"

case "${SHELL:-}" in
  */zsh) RC="$HOME/.zshrc" ;;
  */bash) RC="$HOME/.bashrc" ;;
  *) RC="$HOME/.profile" ;;
esac
if [ -f "$RC" ] && ! grep -Fq "$BIN_DIR" "$RC" 2>/dev/null; then
  printf '\n# Takton\nexport PATH="%s:$PATH"\n' "$BIN_DIR" >>"$RC"
  info "PATH 已写入 $RC"
fi

bold "安装完成"
info "启动: $BIN_DIR/takton start"
info "打开: http://127.0.0.1:$TAKTON_PORT"
info "提示: 环境在 $VENV（本机生成，勿跨机拷贝 venv）"

if [ "$TAKTON_NO_START" = "1" ]; then exit 0; fi

set -a
# shellcheck disable=SC1091
source "$ENV_FILE"
set +a
export PYTHONPATH="$SRC${PYTHONPATH:+:$PYTHONPATH}" PYTHONNOUSERSITE=1
if command -v xdg-open >/dev/null 2>&1; then
  (sleep 2; xdg-open "http://127.0.0.1:$TAKTON_PORT" >/dev/null 2>&1 || true) &
elif command -v open >/dev/null 2>&1; then
  (sleep 2; open "http://127.0.0.1:$TAKTON_PORT" >/dev/null 2>&1 || true) &
fi
exec "$VENV/bin/python" -m backend.cli start --host 127.0.0.1 --port "$TAKTON_PORT"
