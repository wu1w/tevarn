#!/usr/bin/env bash
# Takton one-click installer (Linux) — desktop AppImage client
#   curl -fsSL https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.sh | tr -d '\015' | bash
#
# Downloads AppImage from GitHub Releases. Not a browser-only stack.

set -euo pipefail

REPO="${TAKTON_REPO:-wu1w/takton}"
# strip git url to owner/name
if [[ "$REPO" =~ github.com[:/]([^/]+)/([^/.]+) ]]; then
  REPO="${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
fi
TAG="${TAKTON_RELEASE_TAG:-v0.1.0}"
ASSET="${TAKTON_APPIMAGE_ASSET:-Takton-0.1.0.AppImage}"
NO_START="${TAKTON_NO_START:-0}"
INSTALL_DIR="${TAKTON_HOME:-$HOME/.local/share/takton}"
BIN_DIR="${TAKTON_BIN_DIR:-$HOME/.local/bin}"

info() { printf '[takton] %s\n' "$*" >&2; }
ok()   { printf '[takton] OK %s\n' "$*" >&2; }
err()  { printf '[takton] ERROR: %s\n' "$*" >&2; }

bold() { printf '\033[1m%s\033[0m\n' "$*" >&2; }

bold "Takton desktop client — one-click install"
info "Install dir: $INSTALL_DIR"

mkdir -p "$INSTALL_DIR" "$BIN_DIR"
APP="$INSTALL_DIR/$ASSET"

URLS=(
  "https://github.com/${REPO}/releases/download/${TAG}/${ASSET}"
  "https://github.com/${REPO}/releases/latest/download/${ASSET}"
)

downloaded=0
for url in "${URLS[@]}"; do
  info "Downloading: $url"
  if command -v curl >/dev/null 2>&1; then
    if curl -fL --progress-bar -o "$APP" "$url"; then
      downloaded=1
      break
    fi
  elif command -v wget >/dev/null 2>&1; then
    if wget -O "$APP" "$url"; then
      downloaded=1
      break
    fi
  else
    err "Need curl or wget"
    exit 1
  fi
done

if [[ "$downloaded" -ne 1 ]] || [[ ! -s "$APP" ]]; then
  err "Failed to download AppImage."
  err "Open https://github.com/${REPO}/releases and download ${ASSET} manually."
  exit 1
fi

chmod +x "$APP"
ok "AppImage ready: $APP"

# wrapper
WRAPPER="$BIN_DIR/takton"
cat >"$WRAPPER" <<EOF
#!/usr/bin/env bash
exec "$APP" "\$@"
EOF
chmod +x "$WRAPPER"
ok "Command: $WRAPPER (ensure $BIN_DIR is on PATH)"

# optional desktop entry
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APP_DIR"
cat >"$APP_DIR/takton.desktop" <<EOF
[Desktop Entry]
Name=Takton
Comment=Takton Agent desktop client
Exec=$APP
Terminal=false
Type=Application
Categories=Utility;
EOF
ok "Desktop entry: $APP_DIR/takton.desktop"

if [[ "$NO_START" != "1" ]]; then
  info "Launching Takton..."
  if command -v nohup >/dev/null 2>&1; then
    nohup "$APP" >/dev/null 2>&1 &
  else
    "$APP" >/dev/null 2>&1 &
  fi
fi

ok "Done. Use the Takton desktop app."
