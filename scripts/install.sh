#!/usr/bin/env bash
# Takton one-line installer for Linux / macOS
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.sh | bash
#   # or from a local checkout:
#   bash scripts/install.sh
#
# Env overrides:
#   TAKTON_HOME      install dir (default: $HOME/.takton)
#   TAKTON_REPO      git url (default: https://github.com/wu1w/takton.git)
#   TAKTON_REF       git ref (default: main)
#   TAKTON_PORT      listen port (default: 8090)
#   TAKTON_NO_START  set to 1 to install only
#   TAKTON_SOURCE    path to existing source tree (skip git clone)

set -euo pipefail

TAKTON_HOME="${TAKTON_HOME:-$HOME/.takton}"
TAKTON_REPO="${TAKTON_REPO:-https://github.com/wu1w/takton.git}"
TAKTON_REF="${TAKTON_REF:-main}"
TAKTON_PORT="${TAKTON_PORT:-8090}"
TAKTON_NO_START="${TAKTON_NO_START:-0}"
VENV="$TAKTON_HOME/venv"
SRC="$TAKTON_HOME/src"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '[takton] %s\n' "$*"; }
err()  { printf '[takton] ERROR: %s\n' "$*" >&2; }

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    err "需要命令: $1"
    exit 1
  fi
}

pick_python() {
  # Prefer 3.11–3.13; skip 3.14+ until wheels mature (pydantic-core etc.)
  for c in python3.12 python3.11 python3.13 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1; then
      ver=$("$c" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || true)
      major=${ver%%.*}
      minor=${ver#*.}
      if [ "${major:-0}" -eq 3 ] && [ "${minor:-0}" -ge 10 ] && [ "${minor:-0}" -le 13 ]; then
        echo "$c"
        return 0
      fi
    fi
  done
  return 1
}

bold "Takton installer (Linux / macOS)"

need_cmd curl
need_cmd git

PY="$(pick_python || true)"
if [ -z "${PY:-}" ]; then
  err "需要 Python >= 3.10。请先安装 python3。"
  exit 1
fi
info "Using Python: $PY ($($PY --version 2>&1))"

mkdir -p "$TAKTON_HOME"

if [ -n "${TAKTON_SOURCE:-}" ]; then
  SRC="$(cd "$TAKTON_SOURCE" && pwd)"
  info "Using existing source: $SRC"
elif [ -f "$(pwd)/backend/main.py" ] && [ -f "$(pwd)/pyproject.toml" ]; then
  SRC="$(pwd)"
  info "Using current directory as source: $SRC"
else
  if [ -d "$SRC/.git" ]; then
    info "Updating $SRC ($TAKTON_REF) ..."
    git -C "$SRC" fetch --depth 1 origin "$TAKTON_REF"
    git -C "$SRC" checkout -q FETCH_HEAD
  else
    info "Cloning $TAKTON_REPO ($TAKTON_REF) → $SRC"
    rm -rf "$SRC"
    git clone --depth 1 --branch "$TAKTON_REF" "$TAKTON_REPO" "$SRC" \
      || git clone --depth 1 "$TAKTON_REPO" "$SRC"
  fi
fi

if [ ! -f "$SRC/backend/main.py" ]; then
  err "源码不完整: $SRC/backend/main.py 不存在"
  exit 1
fi

info "Creating venv at $VENV"
"$PY" -m venv "$VENV"
# shellcheck disable=SC1091
. "$VENV/bin/activate"
python -m pip install -U pip setuptools wheel -q

info "Installing Takton (editable) ..."
# Prefer prod requirements for speed/size
if [ -f "$SRC/backend/requirements-prod.txt" ]; then
  pip install -r "$SRC/backend/requirements-prod.txt" -q
fi
pip install -e "$SRC" -q

# Frontend static: build if Node available and static missing
STATIC_INDEX="$SRC/backend/static/index.html"
if [ ! -f "$STATIC_INDEX" ]; then
  if command -v npm >/dev/null 2>&1; then
    info "Building frontend static assets (needs Node/npm) ..."
    if ! takton build; then
      info "前端构建失败 — 仍可 API-only 启动；浏览器 UI 需稍后: takton build"
    fi
  else
    info "未检测到 npm，跳过前端构建。安装 Node.js 后执行: takton build"
    info "或将预构建的 static 放到: $SRC/backend/static/"
  fi
fi

# Secrets / env
ENV_FILE="$TAKTON_HOME/.env"
if [ ! -f "$ENV_FILE" ]; then
  info "Writing $ENV_FILE"
  JWT=$(python -c 'import secrets; print(secrets.token_hex(32))')
  API=$(python -c 'import secrets; print(secrets.token_hex(32))')
  SALT=$(python -c 'import secrets; print(secrets.token_hex(16))')
  DB_PATH="$TAKTON_HOME/data/takton.db"
  mkdir -p "$TAKTON_HOME/data" "$TAKTON_HOME/data/uploads" "$TAKTON_HOME/data/workspace"
  cat >"$ENV_FILE" <<EOF
TAKTON_JWT_SECRET=$JWT
TAKTON_API_KEY=$API
TAKTON_SETTINGS_ENCRYPTION_SALT=$SALT
TAKTON_DB_URL=sqlite+aiosqlite:///$DB_PATH
TAKTON_APP_HOST=127.0.0.1
TAKTON_APP_PORT=$TAKTON_PORT
TAKTON_SINGLE_USER_MODE=true
TAKTON_UPLOADS_DIR=$TAKTON_HOME/data/uploads
TAKTON_FILE_BROWSER_ROOT=$TAKTON_HOME/data/workspace
TAKTON_LOG_LEVEL=info
EOF
fi

# Convenience launcher
BIN_DIR="$TAKTON_HOME/bin"
mkdir -p "$BIN_DIR"
cat >"$BIN_DIR/takton" <<EOF
#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091
source "$VENV/bin/activate"
set -a
# shellcheck disable=SC1091
source "$ENV_FILE"
set +a
export PYTHONPATH="$SRC\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$VENV/bin/takton" "\$@"
EOF
chmod +x "$BIN_DIR/takton"

# PATH hint
SHELL_RC=""
case "${SHELL:-}" in
  */zsh) SHELL_RC="$HOME/.zshrc" ;;
  */bash) SHELL_RC="$HOME/.bashrc" ;;
  *) SHELL_RC="$HOME/.profile" ;;
esac
PATH_LINE="export PATH=\"$BIN_DIR:\$PATH\""
if [ -f "$SHELL_RC" ] && ! grep -Fq "$BIN_DIR" "$SHELL_RC" 2>/dev/null; then
  echo "" >>"$SHELL_RC"
  echo "# Takton" >>"$SHELL_RC"
  echo "$PATH_LINE" >>"$SHELL_RC"
  info "Added $BIN_DIR to PATH via $SHELL_RC"
fi

bold "Install complete."
info "Command: $BIN_DIR/takton start"
info "Open:    http://127.0.0.1:$TAKTON_PORT"
info "Config:  $ENV_FILE"

if [ "$TAKTON_NO_START" = "1" ]; then
  exit 0
fi

info "Starting Takton ..."
# shellcheck disable=SC1091
set -a
source "$ENV_FILE"
set +a
export PYTHONPATH="$SRC${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/takton" start --host 127.0.0.1 --port "$TAKTON_PORT"
