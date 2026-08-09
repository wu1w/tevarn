#!/usr/bin/env bash
# Build tevarn-kernel-host and stage vendor/ for product discovery.
# Usage: ./scripts/build-kernel-host.sh [--release]

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROFILE="debug"
CARGO_FLAG=""
if [[ "${1:-}" == "--release" || "${1:-}" == "-Release" ]]; then
  PROFILE="release"
  CARGO_FLAG="--release"
fi

cargo build -p tevarn-kernel-host $CARGO_FLAG

BUILT="$ROOT/target/$PROFILE/tevarn-kernel-host"
if [[ ! -f "$BUILT" ]]; then
  BUILT="$ROOT/target/$PROFILE/tevarn-kernel-host.exe"
fi
if [[ ! -f "$BUILT" ]]; then
  echo "build finished but binary missing under target/$PROFILE" >&2
  exit 1
fi

VENDOR="$ROOT/vendor/tevarn-kernel-host"
mkdir -p "$VENDOR"
DEST_NAME="$(basename "$BUILT")"
cp -f "$BUILT" "$VENDOR/$DEST_NAME"
cat > "$VENDOR/STAGED.json" <<EOF
{
  "staged_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "source": "$BUILT",
  "dest": "$VENDOR/$DEST_NAME",
  "profile": "$PROFILE"
}
EOF
echo "OK: $BUILT"
echo "OK: staged $VENDOR/$DEST_NAME"
