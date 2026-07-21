#!/usr/bin/env bash
# Takton one-click installer (Linux) — desktop AppImage client
#   curl -fsSL https://raw.githubusercontent.com/wu1w/takton/main/scripts/install.sh | tr -d '\r' | bash
#
# Downloads AppImage from the latest GitHub Release (or TAKTON_RELEASE_TAG).

set -euo pipefail

REPO="${TAKTON_REPO:-wu1w/takton}"
if [[ "$REPO" =~ github.com[:/]([^/]+)/([^/.]+) ]]; then
  REPO="${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
fi
TAG_OVERRIDE="${TAKTON_RELEASE_TAG:-}"
ASSET_OVERRIDE="${TAKTON_APPIMAGE_ASSET:-}"
NO_START="${TAKTON_NO_START:-0}"
INSTALL_DIR="${TAKTON_HOME:-$HOME/.local/share/takton}"
BIN_DIR="${TAKTON_BIN_DIR:-$HOME/.local/bin}"

info() { printf '[takton] %s\n' "$*" >&2; }
ok()   { printf '[takton] OK %s\n' "$*" >&2; }
err()  { printf '[takton] ERROR: %s\n' "$*" >&2; }
bold() { printf '\033[1m%s\033[0m\n' "$*" >&2; }

resolve_latest() {
  local api json tag name url
  if [[ -n "$TAG_OVERRIDE" ]]; then
    api="https://api.github.com/repos/${REPO}/releases/tags/${TAG_OVERRIDE}"
  else
    api="https://api.github.com/repos/${REPO}/releases/latest"
  fi
  info "Resolving release via $api"
  json="$(curl -fsSL -H 'Accept: application/vnd.github+json' -H 'User-Agent: takton-install.sh' "$api")"
  tag="$(printf '%s' "$json" | python3 -c 'import sys,json; print(json.load(sys.stdin)["tag_name"])' 2>/dev/null \
    || printf '%s' "$json" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
  if [[ -n "$ASSET_OVERRIDE" ]]; then
    name="$ASSET_OVERRIDE"
  else
    name="$(printf '%s' "$json" | python3 -c '
import sys,json,re
assets=json.load(sys.stdin).get("assets") or []
for a in assets:
    n=a.get("name") or ""
    if re.match(r"Takton-.*\.AppImage$", n):
        print(n); break
' 2>/dev/null || true)"
    if [[ -z "$name" ]]; then
      name="$(printf '%s' "$json" | grep -oE 'Takton-[^"[:space:]]+\.AppImage' | head -1 || true)"
    fi
  fi
  if [[ -z "$tag" || -z "$name" ]]; then
    return 1
  fi
  url="https://github.com/${REPO}/releases/download/${tag}/${name}"
  printf '%s\n%s\n%s\n' "$tag" "$name" "$url"
}

bold "Takton desktop client — one-click install"
info "Install dir: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR" "$BIN_DIR"

TAG="v0.2.3"
ASSET="Takton-0.2.3.AppImage"
URL=""
if resolved="$(resolve_latest)"; then
  TAG="$(printf '%s\n' "$resolved" | sed -n '1p')"
  ASSET="$(printf '%s\n' "$resolved" | sed -n '2p')"
  URL="$(printf '%s\n' "$resolved" | sed -n '3p')"
  ok "Release ${TAG} → ${ASSET}"
else
  info "API resolve failed; falling back to ${TAG}/${ASSET}"
  URL="https://github.com/${REPO}/releases/download/${TAG}/${ASSET}"
fi

APP="$INSTALL_DIR/$ASSET"
URLS=(
  "$URL"
  "https://github.com/${REPO}/releases/latest/download/${ASSET}"
  "https://github.com/${REPO}/releases/download/${TAG}/${ASSET}"
)

downloaded=0
# unique urls
declare -A seen=()
for url in "${URLS[@]}"; do
  [[ -z "$url" ]] && continue
  [[ -n "${seen[$url]+x}" ]] && continue
  seen[$url]=1
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
ok "AppImage ready: $APP ($(du -h "$APP" | awk '{print $1}'))"

WRAPPER="$BIN_DIR/takton"
cat >"$WRAPPER" <<EOF
#!/usr/bin/env bash
exec "$APP" "\$@"
EOF
chmod +x "$WRAPPER"
ok "Command: $WRAPPER (ensure $BIN_DIR is on PATH)"

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

ok "Done. Installed ${TAG}."
