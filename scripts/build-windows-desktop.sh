#!/usr/bin/env bash
# Build Windows one-click installer (NSIS) + portable exe from Linux or Windows.
# Usage (repo root):
#   bash scripts/build-windows-desktop.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export CSC_IDENTITY_AUTO_DISCOVERY=false
export ELECTRON_BUILDER_CACHE="${ELECTRON_BUILDER_CACHE:-$ROOT/.cache/electron-builder}"
export TEVARN_SKIP_VENDOR_HOST="${TEVARN_SKIP_VENDOR_HOST:-1}"

echo "[tevarn] == Windows desktop pack 0.5.5-alpha =="

# Strip packager/dev API keys & OAuth tokens; refuse secret files in tree
echo "[tevarn] pack-sanitize (no developer credentials in artifact)..."
# Unset common secrets so electron-builder / child processes cannot inherit them
for k in OPENAI_API_KEY ANTHROPIC_API_KEY AZURE_OPENAI_API_KEY GOOGLE_API_KEY GEMINI_API_KEY \
  XAI_API_KEY GROK_API_KEY COHERE_API_KEY MISTRAL_API_KEY TOGETHER_API_KEY FIREWORKS_API_KEY \
  DEEPSEEK_API_KEY HF_TOKEN HUGGINGFACE_HUB_TOKEN TEVARN_LLM_API_KEY TEVARN_EMBEDDING_API_KEY \
  TEVARN_RERANKER_API_KEY TEVARN_IMAGE_API_KEY TEVARN_OPENAI_CHATGPT_ACCOUNT_ID LLM_API_KEY \
  TEVARN_ENV_FILE TEVARN_JWT_SECRET TEVARN_API_KEY TEVARN_SETTINGS_ENCRYPTION_SALT; do
  unset "$k" || true
done
export TEVARN_LOAD_DOTENV=0
node "$ROOT/scripts/pack-sanitize-env.mjs"

echo "[tevarn] free disk:"; df -h "$ROOT" | tail -1

# 1) Windows embed Python + wheels
echo "[tevarn] preparing win-python (cross)..."
node scripts/prepare-win-python-cross.js

if [[ ! -f win-python/python.exe ]]; then
  echo "[tevarn] ERROR: win-python/python.exe missing" >&2
  exit 1
fi
if [[ ! -d win-python/Lib/site-packages/uvicorn ]]; then
  echo "[tevarn] ERROR: uvicorn not in win-python site-packages" >&2
  exit 1
fi

# 2) Optional kernel host (Windows .exe). Skip if absent — backend falls back to python kernel.
if [[ ! -f vendor/tevarn-kernel-host/tevarn-kernel-host.exe ]]; then
  echo "[tevarn] note: no Windows kernel host exe — packing with TEVARN_SKIP_VENDOR_HOST=1 (python kernel)"
  export TEVARN_SKIP_VENDOR_HOST=1
  mkdir -p vendor/tevarn-kernel-host
  echo "Python kernel fallback for this build" > vendor/tevarn-kernel-host/README.md
fi

# 3) Frontend deps + static export + electron main
cd "$ROOT/frontend"
if [[ ! -d node_modules/electron-builder ]] || [[ ! -d node_modules/next ]]; then
  echo "[tevarn] npm ci/install in frontend..."
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

echo "[tevarn] NEXT_EXPORT static build..."
cross_env() { npx cross-env "$@"; }
NEXT_EXPORT=1 npx cross-env NEXT_EXPORT=1 npm run build

echo "[tevarn] compile electron main..."
npx tsc -p ../electron/tsconfig.json

if [[ ! -f dist/index.html ]]; then
  echo "[tevarn] ERROR: frontend/dist/index.html missing" >&2
  exit 1
fi
if [[ ! -f ../electron/dist/main.js ]]; then
  echo "[tevarn] ERROR: electron/dist/main.js missing" >&2
  exit 1
fi

# 4) electron-builder Windows targets
# Prefer nsis (one-click) + portable. On Linux, nsis needs wine — fall back to portable+zip.
TARGETS="nsis,portable"
if ! command -v wine64 >/dev/null 2>&1 && ! command -v wine >/dev/null 2>&1; then
  echo "[tevarn] wine not found — building portable + zip (still double-clickable .exe)"
  TARGETS="portable,zip"
fi

echo "[tevarn] electron-builder --win $TARGETS ..."
npx electron-builder --win --x64 -c.win.target=portable -c.win.target=zip || {
  echo "[tevarn] multi-target failed, try portable only..."
  npx electron-builder --win portable --x64
}

echo "[tevarn] artifacts:"
ls -lah release/ 2>/dev/null || true
# copy to repo-level release for convenience
mkdir -p "$ROOT/release-win"
cp -a release/* "$ROOT/release-win/" 2>/dev/null || true
ls -lah "$ROOT/release-win" 2>/dev/null || ls -lah release/

echo "[tevarn] DONE. Give users the portable .exe or zip; no Python install required."
