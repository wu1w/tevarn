#!/usr/bin/env bash
# Build Windows one-click installer (NSIS) + portable exe from Linux or Windows.
# Usage (repo root):
#   bash scripts/build-windows-desktop.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export CSC_IDENTITY_AUTO_DISCOVERY=false
export ELECTRON_BUILDER_CACHE="${ELECTRON_BUILDER_CACHE:-$ROOT/.cache/electron-builder}"
export TAKTON_SKIP_VENDOR_HOST="${TAKTON_SKIP_VENDOR_HOST:-1}"

echo "[takton] == Windows desktop pack 0.5.5-alpha =="
echo "[takton] free disk:"; df -h "$ROOT" | tail -1

# 1) Windows embed Python + wheels
echo "[takton] preparing win-python (cross)..."
node scripts/prepare-win-python-cross.js

if [[ ! -f win-python/python.exe ]]; then
  echo "[takton] ERROR: win-python/python.exe missing" >&2
  exit 1
fi
if [[ ! -d win-python/Lib/site-packages/uvicorn ]]; then
  echo "[takton] ERROR: uvicorn not in win-python site-packages" >&2
  exit 1
fi

# 2) Optional kernel host (Windows .exe). Skip if absent — backend falls back to python kernel.
if [[ ! -f vendor/takton-kernel-host/takton-kernel-host.exe ]]; then
  echo "[takton] note: no Windows kernel host exe — packing with TAKTON_SKIP_VENDOR_HOST=1 (python kernel)"
  export TAKTON_SKIP_VENDOR_HOST=1
  mkdir -p vendor/takton-kernel-host
  echo "Python kernel fallback for this build" > vendor/takton-kernel-host/README.md
fi

# 3) Frontend deps + static export + electron main
cd "$ROOT/frontend"
if [[ ! -d node_modules/electron-builder ]] || [[ ! -d node_modules/next ]]; then
  echo "[takton] npm ci/install in frontend..."
  if [[ -f package-lock.json ]]; then
    npm ci --no-audit --no-fund || npm install --no-audit --no-fund
  else
    npm install --no-audit --no-fund
  fi
fi

# Sync version
node -e "
const fs=require('fs');
const p='package.json';
const d=JSON.parse(fs.readFileSync(p,'utf8'));
d.version='0.5.5-alpha';
fs.writeFileSync(p, JSON.stringify(d,null,2)+'\n');
console.log('frontend version', d.version);
"

echo "[takton] NEXT_EXPORT static build..."
cross_env() { npx cross-env "$@"; }
NEXT_EXPORT=1 npx cross-env NEXT_EXPORT=1 npm run build

echo "[takton] compile electron main..."
npx tsc -p ../electron/tsconfig.json

if [[ ! -f dist/index.html ]]; then
  echo "[takton] ERROR: frontend/dist/index.html missing" >&2
  exit 1
fi
if [[ ! -f ../electron/dist/main.js ]]; then
  echo "[takton] ERROR: electron/dist/main.js missing" >&2
  exit 1
fi

# 4) electron-builder Windows targets
# Prefer nsis (one-click) + portable. On Linux, nsis needs wine — fall back to portable+zip.
TARGETS="nsis,portable"
if ! command -v wine64 >/dev/null 2>&1 && ! command -v wine >/dev/null 2>&1; then
  echo "[takton] wine not found — building portable + zip (still double-clickable .exe)"
  TARGETS="portable,zip"
fi

echo "[takton] electron-builder --win $TARGETS ..."
npx electron-builder --win --x64 -c.win.target=portable -c.win.target=zip || {
  echo "[takton] multi-target failed, try portable only..."
  npx electron-builder --win portable --x64
}

echo "[takton] artifacts:"
ls -lah release/ 2>/dev/null || true
# copy to repo-level release for convenience
mkdir -p "$ROOT/release-win"
cp -a release/* "$ROOT/release-win/" 2>/dev/null || true
ls -lah "$ROOT/release-win" 2>/dev/null || ls -lah release/

echo "[takton] DONE. Give users the portable .exe or zip; no Python install required."
